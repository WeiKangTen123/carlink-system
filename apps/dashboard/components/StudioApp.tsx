"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { type ReportSummary, type ReportDetail, fileUrl } from "@/lib/api";

// Case data repository
export type CaseData = {
  id: string;
  plate: string;
  vehicle: string;
  accidentType: string;
  severity: string;
  severityClass: "severe" | "moderate" | "minor";
  location: string;
  date: string;
  channel: string;
  aiConfidence: string;
  aiSummary: string;
  keyFactors: string[];
  blueprintHotspots: {
    id: number;
    top: string;
    left: string;
    label: string;
    title: string;
    severe?: boolean;
  }[];
  photos: {
    id: string;
    src: string;
    title: string;
    category: string;
    boxes: {
      top: string;
      left: string;
      width: string;
      height: string;
      tag: string;
      color?: string;
    }[];
  }[];
  damageItems: {
    part: string;
    oem: string;
    desc: string;
    mechanism: string;
    severity: string;
    aiScore: string;
    verified: boolean;
  }[];
  costs: {
    label: string;
    amount: string;
    total?: boolean;
  }[];
  police: {
    station: string;
    ref: string;
  };
  insurance: {
    company: string;
    policy: string;
    type: string;
  };
};

const DEFAULT_CASES: Record<string, CaseData> = {
  "SLK-3063-Z": {
    id: "CL-11900-SLK3063Z",
    plate: "SLK 3063 Z",
    vehicle: "Honda Vezel 1.5 Hybrid",
    accidentType: "Rear-Left Corner Collision",
    severity: "Moderate Damage",
    severityClass: "moderate",
    location: "Tuas Bay Drive, Singapore",
    date: "2026-08-18 16:45",
    channel: "Telegram",
    aiConfidence: "97.1%",
    aiSummary:
      "Detected rear-left corner impact. Severe deformation and sheet metal crease on lower tailgate panel, structural buckling on rear-left quarter panel, and abrasive scuffing across rear bumper lower cover. Reverse parking sensor bracket displaced.",
    keyFactors: [
      "Impact Zone: Rear Left Corner",
      "Airbags: Not Deployed",
      "Chassis Subframe: Nominal",
      "Tailgate Gap: Distorted (8mm)",
    ],
    blueprintHotspots: [
      { id: 0, top: "135px", left: "330px", label: "01", title: "P1273082: Rear Bumper Cover Fracture", severe: false },
      { id: 1, top: "115px", left: "320px", label: "02", title: "P1273082: Left Tailgate Panel Crease", severe: true },
      { id: 2, top: "145px", left: "265px", label: "03", title: "P1273084: Rear Left Quarter Panel Buckle", severe: false },
    ],
    photos: [
      {
        id: "P1273082",
        src: "/cases/slk3063z/P1273082.JPG",
        title: "Rear-Left 3/4 Corner Angle",
        category: "Rear Impact",
        boxes: [
          { top: "52%", left: "42%", width: "24%", height: "26%", tag: "⚡ Tailgate Crease // 98.2%", color: "#ef4444" },
          { top: "58%", left: "26%", width: "24%", height: "22%", tag: "⚡ Bumper Scuff & Crush // 96.5%", color: "#f59e0b" },
        ],
      },
      {
        id: "P1273083",
        src: "/cases/slk3063z/P1273083.JPG",
        title: "Rear Full Perspective & Plate",
        category: "Rear Impact",
        boxes: [
          { top: "48%", left: "46%", width: "20%", height: "28%", tag: "⚡ Tailgate Dent // 97.4%", color: "#ef4444" },
        ],
      },
      {
        id: "P1273087",
        src: "/cases/slk3063z/P1273087.JPG",
        title: "Macro: Tailgate & Bumper Seam",
        category: "Macro Close-up",
        boxes: [
          { top: "28%", left: "30%", width: "42%", height: "45%", tag: "⚡ Sheetmetal Tear // 98.9%", color: "#ef4444" },
        ],
      },
      {
        id: "P1273090",
        src: "/cases/slk3063z/P1273090.JPG",
        title: "Rear Right Angle (Baseline)",
        category: "Side / Quarter",
        boxes: [],
      },
      {
        id: "P1273084",
        src: "/cases/slk3063z/P1273084.JPG",
        title: "Rear Left Wheel & Arch Seam",
        category: "Side / Quarter",
        boxes: [
          { top: "42%", left: "22%", width: "32%", height: "36%", tag: "⚡ Arch Misalignment // 94.0%", color: "#f59e0b" },
        ],
      },
      {
        id: "P1273088",
        src: "/cases/slk3063z/P1273088.JPG",
        title: "Macro: Tailgate Gap Distortion",
        category: "Macro Close-up",
        boxes: [
          { top: "34%", left: "36%", width: "32%", height: "35%", tag: "⚡ Gap Distortion > 8mm // 96.1%", color: "#ef4444" },
        ],
      },
    ],
    damageItems: [
      {
        part: "Rear Tailgate / Boot Lid Assembly",
        oem: "OEM-HON-68100-T7A",
        desc: "Ref: P1273082 • Deep dent & paint crack",
        mechanism: "Plastic Deformation / Crease",
        severity: "Severe",
        aiScore: "98.2%",
        verified: true,
      },
      {
        part: "Rear Bumper Lower Cover & Fascia",
        oem: "OEM-HON-71501-T7A",
        desc: "Ref: P1273082 • Scuffing & lower lip fracture",
        mechanism: "Impact Abrasion & Fracture",
        severity: "Moderate",
        aiScore: "96.5%",
        verified: true,
      },
      {
        part: "Rear Left Quarter Panel / Fender",
        oem: "OEM-HON-63700-T7A",
        desc: "Ref: P1273084 • Sheet metal buckled 35mm",
        mechanism: "Buckling & Torsion",
        severity: "Moderate",
        aiScore: "94.0%",
        verified: true,
      },
      {
        part: "Ultrasonic Reverse Parking Sensor (LH)",
        oem: "OEM-HON-39680-TEX",
        desc: "Ref: P1273087 • Sensor bracket displaced",
        mechanism: "Sensor Misalignment",
        severity: "Minor",
        aiScore: "92.5%",
        verified: true,
      },
    ],
    costs: [
      { label: "OEM Replacement Parts (Tailgate + Bumper Cover)", amount: "SGD 1,850.00" },
      { label: "Panel Beating & Realignment Labor (6.0 hrs @ $85/hr)", amount: "SGD 510.00" },
      { label: "Spray Painting (Pearl Metallic • 3 Panels)", amount: "SGD 750.00" },
      { label: "Parking Sensor Calibration & Diagnostics", amount: "SGD 180.00" },
      { label: "Total Estimated Insurance Indemnity", amount: "SGD 3,290.00", total: true },
    ],
    police: { station: "Singapore Traffic Police HQ", ref: "TP/2026/11900-Z" },
    insurance: {
      company: "NTUC Income Insurance Co-operative",
      policy: "INC-99021-VZ",
      type: "Own Damage (OD) • Comprehensive",
    },
  },

  "VAY-4821": {
    id: "CIR-2026-E973",
    plate: "VAY 4821",
    vehicle: "2023 Honda Civic 1.5 Turbo RS",
    accidentType: "Frontal-Offset Collision",
    severity: "Moderate Damage",
    severityClass: "moderate",
    location: "Federal Highway KM 14.2, PJ",
    date: "2026-08-19 14:15",
    channel: "Telegram",
    aiConfidence: "96.4%",
    aiSummary:
      "Identified direct frontal-offset collision against roadside barrier. Severe mechanical deformation detected on front bumper bar, right fender panel buckled, and right LED headlamp casing destroyed. Structural engine subframe appears intact.",
    keyFactors: ["Impact Angle: 25° Right Offset", "Airbags: Not Deployed", "Subframe: Nominal", "Fluid Leakage: Low Risk"],
    blueprintHotspots: [
      { id: 0, top: "76px", left: "62px", label: "01", title: "P01: Front Bumper Crush", severe: true },
      { id: 1, top: "36px", left: "110px", label: "02", title: "P02: Right Front Fender Torsion", severe: false },
      { id: 2, top: "48px", left: "75px", label: "03", title: "P03: Headlamp Lens Shattered", severe: false },
    ],
    photos: [
      {
        id: "P01",
        src: "/cases/sample/car_accident_1.jpg",
        title: "Frontal Impact Perspective",
        category: "Front Damage",
        boxes: [
          { top: "35%", left: "22%", width: "48%", height: "50%", tag: "⚡ Bumper Deform // 97.2%", color: "#ef4444" },
          { top: "20%", left: "65%", width: "28%", height: "35%", tag: "⚡ Fender Scratch // 94.1%", color: "#f59e0b" },
        ],
      },
      {
        id: "P02",
        src: "/cases/sample/car_accident_2.jpg",
        title: "Right Quarter Panel",
        category: "Side Impact",
        boxes: [
          { top: "40%", left: "30%", width: "40%", height: "40%", tag: "⚡ Panel Torsion // 95.0%", color: "#f59e0b" },
        ],
      },
      {
        id: "P03",
        src: "/cases/sample/malaysia_sample_2.jpg",
        title: "License Plate & Underbody",
        category: "Front Damage",
        boxes: [],
      },
      {
        id: "P04",
        src: "/cases/sample/online_sample_1.jpg",
        title: "Wide Angle Overview",
        category: "Wide Angle",
        boxes: [],
      },
    ],
    damageItems: [
      {
        part: "Front Bumper Assembly",
        oem: "OEM-HON-71101-T20",
        desc: "Ref: P01 • Lower lip tear",
        mechanism: "Severe Crush / Tear",
        severity: "Severe",
        aiScore: "97.2%",
        verified: true,
      },
      {
        part: "Right Front Fender Panel",
        oem: "OEM-HON-60211-T20",
        desc: "Ref: P02 • Panel buckled 40mm",
        mechanism: "Buckling & Torsion",
        severity: "Moderate",
        aiScore: "94.1%",
        verified: true,
      },
      {
        part: "Right LED Headlamp Unit",
        oem: "OEM-HON-33100-T20",
        desc: "Ref: P01 • Lens shattered",
        mechanism: "Lens Shattered",
        severity: "Moderate",
        aiScore: "98.5%",
        verified: true,
      },
    ],
    costs: [
      { label: "OEM Replacement Parts (Bumper + Headlamp)", amount: "RM 2,450.00" },
      { label: "Panel Beating & Alignment Labor (5.5 hrs)", amount: "RM 850.00" },
      { label: "Spray Painting (2-Stage Pearl Coat • 2 Panels)", amount: "RM 900.00" },
      { label: "ADAS Radar Calibration & Diagnostics", amount: "RM 200.00" },
      { label: "Total Estimated Insurance Indemnity", amount: "RM 4,400.00", total: true },
    ],
    police: { station: "Balai Polis Trafik Petaling Jaya", ref: "PJ/TRAF/2026/08912" },
    insurance: { company: "Allianz General Insurance Bhd", policy: "ALZ-99214-08", type: "Own Damage (OD)" },
  },

  "WX-8888-A": {
    id: "CIR-2026-F7A3",
    plate: "WX 8888 A",
    vehicle: "2021 Toyota Hilux 2.8 D-4D",
    accidentType: "Rear-End Barrier Collision",
    severity: "Minor Damage",
    severityClass: "minor",
    location: "Workshop Bay 2, Subang",
    date: "2026-08-15 11:20",
    channel: "WhatsApp",
    aiConfidence: "95.8%",
    aiSummary:
      "Detected minor reverse impact against parking curb. Rear steel step bumper scratched, left mudflap dislodged, minimal cosmetic paint scuffing.",
    keyFactors: ["Impact Zone: Rear Bumper Step", "Chassis: Intact", "Bed Alignment: Nominal"],
    blueprintHotspots: [
      { id: 0, top: "135px", left: "320px", label: "01", title: "P01: Steel Bumper Step Scratch", severe: false },
    ],
    photos: [
      {
        id: "P01",
        src: "/cases/sample/malaysia_sample_2.jpg",
        title: "Rear Bumper Step",
        category: "Rear Impact",
        boxes: [
          { top: "45%", left: "35%", width: "30%", height: "30%", tag: "⚡ Step Scuff // 95.8%", color: "#10b981" },
        ],
      },
    ],
    damageItems: [
      {
        part: "Rear Step Bumper Assembly",
        oem: "OEM-TOY-52151-0K0",
        desc: "Ref: P01 • Minor cosmetic scratch",
        mechanism: "Surface Abrasion",
        severity: "Minor",
        aiScore: "95.8%",
        verified: true,
      },
    ],
    costs: [
      { label: "Rear Step Buffing & Touch-Up Paint", amount: "RM 350.00" },
      { label: "Mudflap Clip Replacement", amount: "RM 60.00" },
      { label: "Total Estimated Insurance Indemnity", amount: "RM 410.00", total: true },
    ],
    police: { station: "Balai Polis Subang Jaya", ref: "SJ/2026/0411" },
    insurance: { company: "Etiqa General Insurance Bhd", policy: "ETQ-55219-HL", type: "Own Damage (OD)" },
  },
};

interface StudioAppProps {
  initialReports?: ReportSummary[];
  initialCaseKey?: string;
}

export function StudioApp({ initialReports = [], initialCaseKey = "SLK-3063-Z" }: StudioAppProps) {
  const [activeTab, setActiveTab] = useState<"studio" | "overview" | "wizard" | "analytics">("studio");
  const [currentCaseKey, setCurrentCaseKey] = useState<string>(initialCaseKey);
  const [activePhotoIndex, setActivePhotoIndex] = useState<number>(0);
  const [activeCategory, setActiveCategory] = useState<string>("All Photos");
  const [showOverlay, setShowOverlay] = useState<boolean>(true);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [isPdfModalOpen, setIsPdfModalOpen] = useState<boolean>(false);
  const [isSignOffModalOpen, setIsSignOffModalOpen] = useState<boolean>(false);
  const [isSignedOff, setIsSignedOff] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [severityFilter, setSeverityFilter] = useState<string>("all");
  const [verifiedItems, setVerifiedItems] = useState<Record<number, boolean>>({});

  useEffect(() => {
    const saved = (localStorage.getItem("carlink-theme") as "dark" | "light") || "dark";
    setTheme(saved);
    document.documentElement.setAttribute("data-theme", saved);
  }, []);

  const toggleThemeMode = (mode: "dark" | "light") => {
    setTheme(mode);
    document.documentElement.setAttribute("data-theme", mode);
    localStorage.setItem("carlink-theme", mode);
  };

  const caseData = DEFAULT_CASES[currentCaseKey] || DEFAULT_CASES["SLK-3063-Z"];
  const currentPhoto = caseData.photos[activePhotoIndex] || caseData.photos[0];
  const photoCategories = ["All Photos", ...Array.from(new Set(caseData.photos.map((p) => p.category)))];

  const handleSelectCase = (key: string) => {
    setCurrentCaseKey(key);
    setActivePhotoIndex(0);
    setActiveCategory("All Photos");
    setIsSignedOff(false);
    setVerifiedItems({});
  };

  const toggleItemVerify = (idx: number) => {
    setVerifiedItems((prev) => ({
      ...prev,
      [idx]: prev[idx] !== undefined ? !prev[idx] : false,
    }));
  };

  return (
    <div style={{ paddingBottom: 48 }}>
      {/* Top Navbar Studio Bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          background: "var(--surface-card)",
          backdropFilter: "blur(20px)",
          border: "1px solid var(--border-color)",
          borderRadius: "14px",
          padding: "10px 18px",
          marginBottom: "20px",
          boxShadow: "var(--shadow-md)",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        {/* Navigation Tabs */}
        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
          <button
            type="button"
            className={`nav-link ${activeTab === "studio" ? "active" : ""}`}
            onClick={() => setActiveTab("studio")}
            style={{ cursor: "pointer", border: "none" }}
          >
            <span>🔍</span> Loss Adjuster Studio
          </button>
          <button
            type="button"
            className={`nav-link ${activeTab === "overview" ? "active" : ""}`}
            onClick={() => setActiveTab("overview")}
            style={{ cursor: "pointer", border: "none" }}
          >
            <span>📊</span> Command Center
          </button>
          <button
            type="button"
            className={`nav-link ${activeTab === "wizard" ? "active" : ""}`}
            onClick={() => setActiveTab("wizard")}
            style={{ cursor: "pointer", border: "none" }}
          >
            <span>✨</span> Incident Intake
          </button>
          <button
            type="button"
            className={`nav-link ${activeTab === "analytics" ? "active" : ""}`}
            onClick={() => setActiveTab("analytics")}
            style={{ cursor: "pointer", border: "none" }}
          >
            <span>📈</span> AI Analytics
          </button>
        </div>

        {/* Action Controls */}
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {/* Active Case Selector Dropdown */}
          <div className="case-selector-box" title="Switch Incident Case">
            <span style={{ color: "var(--accent-cyan)", fontSize: "11px", fontWeight: 800 }}>CASE:</span>
            <select
              className="case-select-dropdown"
              value={currentCaseKey}
              onChange={(e) => handleSelectCase(e.target.value)}
            >
              <option value="SLK-3063-Z">SLK 3063 Z (Honda Vezel)</option>
              <option value="VAY-4821">VAY 4821 (Honda Civic RS)</option>
              <option value="WX-8888-A">WX 8888 A (Toyota Hilux)</option>
            </select>
          </div>

          {/* Clean Dark & White Theme Switch */}
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              background: "var(--surface-hover)",
              border: "1px solid var(--border-color)",
              padding: "3px",
              borderRadius: "24px",
              gap: "3px",
            }}
          >
            <button
              type="button"
              onClick={() => toggleThemeMode("dark")}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "5px",
                fontSize: "11px",
                fontWeight: 700,
                padding: "4px 10px",
                borderRadius: "18px",
                border: "none",
                background: theme === "dark" ? "linear-gradient(135deg, #38bdf8 0%, #6366f1 100%)" : "transparent",
                color: theme === "dark" ? "#ffffff" : "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              <span>🌙</span> Dark
            </button>
            <button
              type="button"
              onClick={() => toggleThemeMode("light")}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "5px",
                fontSize: "11px",
                fontWeight: 700,
                padding: "4px 10px",
                borderRadius: "18px",
                border: "none",
                background: theme === "light" ? "#ffffff" : "transparent",
                color: theme === "light" ? "#0f172a" : "var(--text-muted)",
                cursor: "pointer",
              }}
            >
              <span>☀️</span> White
            </button>
          </div>

          {/* Live PDF & Sign Off */}
          <button type="button" className="btn-secondary-modern" onClick={() => setIsPdfModalOpen(true)}>
            <span>📄</span> Live PDF
          </button>
          <button type="button" className="btn-primary-modern" onClick={() => setIsSignOffModalOpen(true)}>
            <span>✍️</span> Sign Off
          </button>
        </div>
      </div>

      {/* =========================================================================
          TAB 1: LOSS ADJUSTER LIVE STUDIO (MAIN EXPERIENCE)
          ========================================================================= */}
      {activeTab === "studio" && (
        <div>
          {/* 5-Stage Claim Lifecycle Progress Stepper */}
          <div className="claim-stepper-glass">
            <div className="step-node completed">
              <div className="step-circle">✓</div>
              <div>
                <div className="step-title">1. Bot Ingestion</div>
                <div className="step-desc">{caseData.channel} Intake</div>
              </div>
            </div>

            <div className="step-node completed">
              <div className="step-circle">✓</div>
              <div>
                <div className="step-title">2. AI Vision Scan</div>
                <div className="step-desc">Gemini-2.5 &bull; {caseData.aiConfidence}</div>
              </div>
            </div>

            <div className={`step-node ${isSignedOff ? "completed" : "active"}`}>
              <div className="step-circle">{isSignedOff ? "✓" : "3"}</div>
              <div>
                <div className="step-title">3. Surveyor Audit</div>
                <div className="step-desc">{isSignedOff ? "Audited & Verified" : "Under Review (You)"}</div>
              </div>
            </div>

            <div className={`step-node ${isSignedOff ? "completed" : ""}`}>
              <div className="step-circle">{isSignedOff ? "✓" : "4"}</div>
              <div>
                <div className="step-title">4. Manager Sign-Off</div>
                <div className="step-desc">{isSignedOff ? "Signed & Locked" : "Pending Sign-Off"}</div>
              </div>
            </div>

            <div className="step-node">
              <div className="step-circle">5</div>
              <div>
                <div className="step-title">5. Claim Settled</div>
                <div className="step-desc">{caseData.insurance.company.split(" ")[0]}</div>
              </div>
            </div>
          </div>

          {/* Studio Header Strip */}
          <div className="card-header" style={{ marginBottom: 20 }}>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
                <span className="badge-plate-glow">{caseData.plate}</span>
                <span className={`chip-severity ${caseData.severityClass}`}>
                  {isSignedOff ? "✓ SIGNED OFF & LOCKED" : `⚡ ${caseData.severity}`}
                </span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  CASE_ID: <code>{caseData.id}</code> &bull; Incident at {caseData.location}
                </span>
              </div>
              <h1 style={{ fontSize: 22, fontWeight: 800, letterSpacing: "-0.02em", margin: 0 }}>
                {caseData.vehicle} &mdash; {caseData.accidentType}
              </h1>
            </div>

            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <button
                type="button"
                className="btn-secondary-modern"
                onClick={() => setShowOverlay(!showOverlay)}
              >
                <span>{showOverlay ? "👁️" : "🙈"}</span>
                <span>{showOverlay ? "Hide AI Vision Box" : "Show AI Vision Box"}</span>
              </button>
              <button
                type="button"
                className="btn-primary-modern"
                onClick={() => setIsSignOffModalOpen(true)}
              >
                <span>✓</span> Finalize &amp; Lock Sign-Off
              </button>
            </div>
          </div>

          {/* Split Studio Layout */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1.15fr", gap: 24 }}>
            {/* Left Column: Blueprint & Photo Inspector */}
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {/* Vehicle Body Blueprint */}
              <div className="card-glass">
                <div className="card-header">
                  <div>
                    <div className="card-title">
                      <span>📐</span> Interactive Vehicle Body Blueprint
                    </div>
                    <div className="card-subtitle">
                      Click pulsing damage hotspots to jump directly to evidence photo angles
                    </div>
                  </div>
                  <span className={`chip-severity ${caseData.severityClass}`}>
                    {caseData.blueprintHotspots.length} Damaged Zones
                  </span>
                </div>

                <div className="blueprint-stage-radar">
                  <div className="radar-laser-beam" />
                  <div className="svg-car-container">
                    <svg viewBox="0 0 400 180" width="100%" height="auto" style={{ display: "block" }}>
                      <path
                        d="M 60 90 Q 60 40 100 35 L 140 35 L 180 20 L 260 20 L 300 35 L 340 40 Q 365 90 340 140 L 300 145 L 260 160 L 180 160 L 140 145 L 100 145 Q 60 140 60 90 Z"
                        fill="none"
                        stroke="var(--border-glow, #38bdf8)"
                        strokeWidth="2.2"
                        strokeLinejoin="round"
                      />
                      <path
                        d="M 175 35 L 255 35 L 275 50 L 155 50 Z"
                        fill="rgba(56, 189, 248, 0.08)"
                        stroke="var(--border-color)"
                        strokeWidth="1.5"
                      />
                      <path
                        d="M 175 145 L 255 145 L 275 130 L 155 130 Z"
                        fill="rgba(56, 189, 248, 0.08)"
                        stroke="var(--border-color)"
                        strokeWidth="1.5"
                      />
                      <rect x="180" y="55" width="80" height="70" rx="6" fill="none" stroke="var(--border-color)" strokeWidth="1.5" />
                      <rect x="105" y="14" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
                      <rect x="270" y="14" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
                      <rect x="105" y="150" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
                      <rect x="270" y="150" width="36" height="16" rx="3" fill="var(--text-muted)" opacity="0.4" />
                      <text x="35" y="94" fontFamily="var(--font-mono)" fontSize="9" fontWeight="700" fill="var(--text-muted)" textAnchor="middle">
                        FRONT
                      </text>
                      <text x="365" y="94" fontFamily="var(--font-mono)" fontSize="9" fontWeight="700" fill="var(--text-muted)" textAnchor="middle">
                        REAR
                      </text>
                    </svg>

                    {caseData.blueprintHotspots.map((spot, idx) => (
                      <button
                        key={spot.id || idx}
                        type="button"
                        className={`hotspot-beacon ${spot.severe ? "severe-spot" : ""}`}
                        style={{ top: spot.top, left: spot.left }}
                        title={spot.title}
                        onClick={() => setActivePhotoIndex(idx % caseData.photos.length)}
                      >
                        {spot.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* AI Vision Photo Inspector */}
              <div className="card-glass">
                <div className="card-header">
                  <div>
                    <div className="card-title">
                      <span>📸</span> AI Vision Photo Inspector
                    </div>
                    <div className="card-subtitle">
                      Photo {activePhotoIndex + 1} of {caseData.photos.length} &bull; Photo ID:{" "}
                      <strong style={{ fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>
                        {currentPhoto.id}
                      </strong>
                    </div>
                  </div>
                  <span className="chip-severity minor">{currentPhoto.category}</span>
                </div>

                {/* Main Evidence Photo Display */}
                <div className="photo-inspector-box">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={currentPhoto.src} alt={currentPhoto.id} className="inspector-main-img" />

                  {showOverlay && currentPhoto.boxes && currentPhoto.boxes.length > 0 && (
                    <div className="ai-bounding-overlay">
                      {currentPhoto.boxes.map((box, bIdx) => (
                        <div
                          key={bIdx}
                          className="ai-box-marker-glow"
                          style={{
                            top: box.top,
                            left: box.left,
                            width: box.width,
                            height: box.height,
                            borderColor: box.color || "var(--accent-cyan)",
                          }}
                        >
                          <div
                            className="ai-box-tag-glow"
                            style={{
                              background: box.color
                                ? `linear-gradient(135deg, ${box.color} 0%, #1e293b 100%)`
                                : "var(--accent-gradient)",
                            }}
                          >
                            {box.tag}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Category Chips */}
                <div className="photo-category-strip">
                  {photoCategories.map((cat) => {
                    const count =
                      cat === "All Photos"
                        ? caseData.photos.length
                        : caseData.photos.filter((p) => p.category === cat).length;
                    return (
                      <button
                        key={cat}
                        type="button"
                        className={`photo-cat-btn ${activeCategory === cat ? "active" : ""}`}
                        onClick={() => setActiveCategory(cat)}
                      >
                        {cat} ({count})
                      </button>
                    );
                  })}
                </div>

                {/* Thumbnail Selector Strip */}
                <div className="photo-thumb-strip">
                  {caseData.photos.map((p, idx) => {
                    const isMatch = activeCategory === "All Photos" || p.category === activeCategory;
                    if (!isMatch) return null;
                    return (
                      <div
                        key={p.id || idx}
                        className={`photo-thumb ${activePhotoIndex === idx ? "active" : ""}`}
                        onClick={() => setActivePhotoIndex(idx)}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={p.src} alt={p.id} />
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Right Column: AI Insights & Tables */}
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {/* Gemini Analysis Card */}
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
                    {caseData.aiConfidence} AI Confidence
                  </span>
                </div>

                <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 10 }}>
                  {caseData.aiSummary}
                </p>

                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {caseData.keyFactors.map((f, i) => (
                    <span key={i} className="chip-severity minor" style={{ fontSize: 10 }}>
                      {f}
                    </span>
                  ))}
                </div>
              </div>

              {/* Damage Verification Checklist Table */}
              <div className="card-glass">
                <div className="card-header">
                  <div>
                    <div className="card-title">
                      <span>📋</span> Damage Verification &amp; Parts Checklist
                    </div>
                    <div className="card-subtitle">Surveyor confirmation required for claim approval</div>
                  </div>
                  <span className="chip-severity minor" style={{ fontSize: 10 }}>
                    {caseData.damageItems.length} Components
                  </span>
                </div>

                <table className="damage-table-modern">
                  <thead>
                    <tr>
                      <th>Damaged Component</th>
                      <th>Damage Mechanism</th>
                      <th>Severity</th>
                      <th>AI Score</th>
                      <th>Surveyor Sign</th>
                    </tr>
                  </thead>
                  <tbody>
                    {caseData.damageItems.map((item, idx) => {
                      const isVerified = verifiedItems[idx] !== undefined ? verifiedItems[idx] : item.verified;
                      return (
                        <tr key={idx}>
                          <td>
                            <strong>{item.part}</strong>
                            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                              {item.desc}
                            </div>
                          </td>
                          <td>{item.mechanism}</td>
                          <td>
                            <span className={`chip-severity ${item.severity.toLowerCase()}`}>
                              {item.severity}
                            </span>
                          </td>
                          <td>
                            <strong style={{ fontFamily: "var(--font-mono)" }}>{item.aiScore}</strong>
                          </td>
                          <td>
                            <button
                              type="button"
                              className={`verify-toggle-modern ${isVerified ? "verified" : ""}`}
                              onClick={() => toggleItemVerify(idx)}
                            >
                              {isVerified ? "✓ Verified" : "Pending"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
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

                <div className="cost-matrix-glow">
                  {caseData.costs.map((c, i) => (
                    <div key={i} className={`cost-row ${c.total ? "total" : ""}`}>
                      <span>{c.label}</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>{c.amount}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Police & Insurance Policy Details */}
              <div className="card-glass">
                <div className="card-header">
                  <div className="card-title">
                    <span>🏛️</span> Authority &amp; Insurance Policy Details
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
                  <div>
                    <div className="detail-field-label">Police Station</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>{caseData.police.station}</div>
                  </div>
                  <div>
                    <div className="detail-field-label">Report Reference Number</div>
                    <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>
                      {caseData.police.ref}
                    </div>
                  </div>
                  <div>
                    <div className="detail-field-label">Insurance Provider</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>{caseData.insurance.company}</div>
                  </div>
                  <div>
                    <div className="detail-field-label">Policy &amp; Claim Type</div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>
                      {caseData.insurance.policy} &bull; {caseData.insurance.type}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          TAB 2: COMMAND CENTER (OVERVIEW DASHBOARD)
          ========================================================================= */}
      {activeTab === "overview" && (
        <div>
          {/* KPI Tiles */}
          <div className="kpi-grid-modern">
            <div className="kpi-card-glow">
              <div className="kpi-label">Total Logged Cases</div>
              <div className="kpi-val">42</div>
              <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
                ↑ +14% vs last month
              </div>
            </div>

            <div className="kpi-card-glow">
              <div className="kpi-label">Pending Surveyor Review</div>
              <div className="kpi-val" style={{ color: "var(--badge-amber-text)" }}>
                07
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
                Avg response: 42 mins
              </div>
            </div>

            <div className="kpi-card-glow">
              <div className="kpi-label">Signed-Off Reports</div>
              <div className="kpi-val" style={{ color: "var(--badge-green-text)" }}>
                35
              </div>
              <div style={{ fontSize: "11px", color: "var(--badge-green-text)", fontWeight: 700, marginTop: "4px" }}>
                ✓ 100% compliance
              </div>
            </div>

            <div className="kpi-card-glow">
              <div className="kpi-label">Severe Collision Cases</div>
              <div className="kpi-val" style={{ color: "var(--badge-red-text)" }}>
                04
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px" }}>
                Loss Adjuster Sign-Off Required
              </div>
            </div>
          </div>

          {/* Search & Filter Bar */}
          <div className="card-glass" style={{ marginBottom: 20, padding: "16px 20px" }}>
            <div style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 240 }}>
                <input
                  type="text"
                  placeholder="🔍 Search by Plate (e.g. SLK 3063 Z), Driver, Location, Case ID..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "10px 14px",
                    borderRadius: 8,
                    border: "1px solid var(--border-color)",
                    background: "var(--surface-card)",
                    color: "var(--text-primary)",
                    fontSize: 13,
                    outline: "none",
                  }}
                />
              </div>

              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  className={`photo-cat-btn ${severityFilter === "all" ? "active" : ""}`}
                  onClick={() => setSeverityFilter("all")}
                >
                  All Cases
                </button>
                <button
                  type="button"
                  className={`photo-cat-btn ${severityFilter === "Severe" ? "active" : ""}`}
                  onClick={() => setSeverityFilter("Severe")}
                >
                  Severe
                </button>
                <button
                  type="button"
                  className={`photo-cat-btn ${severityFilter === "Moderate" ? "active" : ""}`}
                  onClick={() => setSeverityFilter("Moderate")}
                >
                  Moderate
                </button>
                <button
                  type="button"
                  className={`photo-cat-btn ${severityFilter === "Minor" ? "active" : ""}`}
                  onClick={() => setSeverityFilter("Minor")}
                >
                  Minor
                </button>
              </div>

              <button
                type="button"
                className="btn-primary-modern"
                onClick={() => setActiveTab("wizard")}
              >
                + File New Incident
              </button>
            </div>
          </div>

          {/* Cases Master Table */}
          <div className="card-glass" style={{ padding: 0 }}>
            <table className="damage-table-modern">
              <thead>
                <tr>
                  <th>Case ID</th>
                  <th>Vehicle Specification</th>
                  <th>Intake Channel</th>
                  <th>Accident Mechanism</th>
                  <th>Incident Location</th>
                  <th>Severity</th>
                  <th>Claim Status</th>
                  <th>Studio Action</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(DEFAULT_CASES).map(([key, item]) => {
                  const matchSearch =
                    item.plate.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    item.vehicle.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    item.location.toLowerCase().includes(searchQuery.toLowerCase());
                  const matchSev = severityFilter === "all" || item.severity.includes(severityFilter);
                  if (!matchSearch || !matchSev) return null;

                  return (
                    <tr
                      key={key}
                      style={{ cursor: "pointer" }}
                      onClick={() => {
                        handleSelectCase(key);
                        setActiveTab("studio");
                      }}
                    >
                      <td>
                        <strong style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>
                          {item.id}
                        </strong>
                      </td>
                      <td>
                        <span className="badge-plate-glow" style={{ fontSize: 11, padding: "2px 8px" }}>
                          {item.plate}
                        </span>
                        <span style={{ fontSize: 12, color: "var(--text-muted)", marginLeft: 6 }}>
                          {item.vehicle}
                        </span>
                      </td>
                      <td>
                        <span className="chip-severity minor" style={{ fontSize: 10 }}>
                          {item.channel}
                        </span>
                      </td>
                      <td>{item.accidentType}</td>
                      <td>{item.location}</td>
                      <td>
                        <span className={`chip-severity ${item.severityClass}`}>{item.severity}</span>
                      </td>
                      <td>
                        <strong style={{ color: "var(--badge-amber-text)" }}>Under Review</strong>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="btn-primary-modern"
                          style={{ fontSize: 11, padding: "4px 10px" }}
                        >
                          Open Studio →
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* =========================================================================
          TAB 3: INCIDENT INTAKE WIZARD
          ========================================================================= */}
      {activeTab === "wizard" && (
        <div style={{ maxWidth: 860, margin: "0 auto" }}>
          <div className="card-glass" style={{ padding: 32 }}>
            <div style={{ marginBottom: 24, borderBottom: "1px solid var(--border-color)", paddingBottom: 14 }}>
              <h2 style={{ fontSize: 18, fontWeight: 800, margin: 0 }}>File New Vehicle Incident Report</h2>
              <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 4 }}>
                Gemini Multimodal Vision will automatically parse evidence photos into normalized claims structures.
              </p>
            </div>

            <div
              style={{
                border: "2px dashed var(--border-hover)",
                borderRadius: "12px",
                padding: "36px 20px",
                textAlign: "center",
                background: "var(--surface-elevated)",
                cursor: "pointer",
                marginBottom: 20,
              }}
              onClick={() => {
                alert("Simulated AI Vision Scan: Extracted 4 Damaged Parts for Honda Vezel (SLK 3063 Z)!");
                handleSelectCase("SLK-3063-Z");
                setActiveTab("studio");
              }}
            >
              <div style={{ fontSize: 36, marginBottom: 8 }}>📸</div>
              <h3 style={{ fontSize: 15, fontWeight: 700 }}>Drop accident photos here or click to browse</h3>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 4 }}>
                Supports JPG, PNG, WEBP &bull; Max 20 High-Res Evidence Photos per Case
              </p>
              <button type="button" className="btn-primary-modern" style={{ marginTop: 14 }}>
                Select Evidence Photos
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <div>
                <label className="detail-field-label">Vehicle Plate Number</label>
                <input
                  type="text"
                  defaultValue="SLK 3063 Z"
                  style={{
                    width: "100%",
                    padding: "10px 14px",
                    borderRadius: 8,
                    border: "1px solid var(--border-color)",
                    background: "var(--surface-card)",
                    color: "var(--text-primary)",
                    marginTop: 4,
                  }}
                />
              </div>
              <div>
                <label className="detail-field-label">Vehicle Make &amp; Model</label>
                <input
                  type="text"
                  defaultValue="Honda Vezel 1.5 Hybrid"
                  style={{
                    width: "100%",
                    padding: "10px 14px",
                    borderRadius: 8,
                    border: "1px solid var(--border-color)",
                    background: "var(--surface-card)",
                    color: "var(--text-primary)",
                    marginTop: 4,
                  }}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 24 }}>
              <button type="button" className="btn-secondary-modern" onClick={() => setActiveTab("studio")}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary-modern"
                onClick={() => {
                  handleSelectCase("SLK-3063-Z");
                  setActiveTab("studio");
                }}
              >
                Process &amp; Open in Loss Adjuster Studio →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          TAB 4: AI ANALYTICS
          ========================================================================= */}
      {activeTab === "analytics" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">Damaged Parts Frequency Heatmap</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, fontSize: 13 }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, marginBottom: 4 }}>
                  <span>Rear Tailgate / Boot Lid Assembly</span>
                  <span>46%</span>
                </div>
                <div style={{ height: 8, background: "var(--surface-elevated)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: "46%", height: "100%", background: "#ef4444" }} />
                </div>
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, marginBottom: 4 }}>
                  <span>Rear Bumper Lower Cover &amp; Fascia</span>
                  <span>38%</span>
                </div>
                <div style={{ height: 8, background: "var(--surface-elevated)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: "38%", height: "100%", background: "#f59e0b" }} />
                </div>
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600, marginBottom: 4 }}>
                  <span>Rear Left Quarter Panel / Fender</span>
                  <span>29%</span>
                </div>
                <div style={{ height: 8, background: "var(--surface-elevated)", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: "29%", height: "100%", background: "var(--accent-cyan)" }} />
                </div>
              </div>
            </div>
          </div>

          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">Surveyor First-Pass Concordance</div>
            </div>
            <div style={{ textAlign: "center", padding: "32px 0" }}>
              <div style={{ fontSize: 52, fontWeight: 800, color: "var(--badge-green-text)", letterSpacing: "-0.03em" }}>
                96.2%
              </div>
              <div style={{ fontSize: 14, fontWeight: 700, marginTop: 4 }}>AI First-Pass Accuracy</div>
              <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 8, maxWidth: 320, margin: "8px auto 0" }}>
                Only 3.8% of damage items required manual surveyor correction before official PDF sign-off.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          MODAL 1: LIVE 20-SECTION OFFICIAL INSURANCE REPORT PREVIEW
          ========================================================================= */}
      {isPdfModalOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0, 0, 0, 0.8)",
            backdropFilter: "blur(10px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
          }}
          onClick={() => setIsPdfModalOpen(false)}
        >
          <div
            style={{
              background: "var(--surface-card)",
              border: "1px solid var(--border-color)",
              borderRadius: "16px",
              width: "90%",
              maxWidth: "900px",
              maxHeight: "90vh",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "16px 24px",
                borderBottom: "1px solid var(--border-color)",
              }}
            >
              <h3 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
                📄 Official Insurance Assessment Certificate ({caseData.id})
              </h3>
              <button
                type="button"
                className="btn-secondary-modern"
                style={{ padding: "4px 10px" }}
                onClick={() => setIsPdfModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <div style={{ padding: "24px", overflowY: "auto", background: "#334155" }}>
              <div
                style={{
                  background: "#ffffff",
                  color: "#0f172a",
                  padding: "36px 42px",
                  borderRadius: "4px",
                  fontFamily: "Arial, sans-serif",
                  fontSize: "12px",
                  lineHeight: 1.5,
                }}
              >
                <div
                  style={{
                    borderBottom: "2px solid #2563eb",
                    paddingBottom: "12px",
                    marginBottom: "16px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-end",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b" }}>
                      CARLINK MOTOR CLAIMS CONSULTANCY
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 800, color: "#1e3a8a" }}>
                      Vehicle Damage Assessment Report
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontFamily: "monospace", fontSize: 13, fontWeight: 800, color: "#1e3a8a" }}>
                      {caseData.id}
                    </div>
                    <div style={{ fontSize: 10, color: "#64748b" }}>Date: {caseData.date}</div>
                  </div>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(4, 1fr)",
                    gap: "12px",
                    background: "#f8fafc",
                    padding: "12px",
                    borderRadius: "6px",
                    marginBottom: "16px",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 9, fontWeight: 700, color: "#64748b" }}>VEHICLE PLATE</div>
                    <div style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 800 }}>{caseData.plate}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9, fontWeight: 700, color: "#64748b" }}>MAKE &amp; MODEL</div>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{caseData.vehicle}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9, fontWeight: 700, color: "#64748b" }}>ACCIDENT TYPE</div>
                    <div style={{ fontSize: 12, fontWeight: 600 }}>{caseData.accidentType}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 9, fontWeight: 700, color: "#64748b" }}>SEVERITY</div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: "#b45309" }}>{caseData.severity}</div>
                  </div>
                </div>

                <div style={{ marginBottom: "16px" }}>
                  <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "#1e3a8a", marginBottom: 6 }}>
                    Verified Damage Schedule ({caseData.damageItems.length} Components)
                  </div>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                    <thead>
                      <tr style={{ background: "#e2e8f0", textAlign: "left" }}>
                        <th style={{ padding: "6px 8px" }}>Component</th>
                        <th style={{ padding: "6px 8px" }}>Damage Mechanism</th>
                        <th style={{ padding: "6px 8px" }}>Severity</th>
                        <th style={{ padding: "6px 8px" }}>AI Confidence</th>
                        <th style={{ padding: "6px 8px" }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {caseData.damageItems.map((it, idx) => (
                        <tr key={idx} style={{ borderBottom: "1px solid #e2e8f0" }}>
                          <td style={{ padding: "6px 8px" }}>{it.part}</td>
                          <td style={{ padding: "6px 8px" }}>{it.mechanism}</td>
                          <td style={{ padding: "6px 8px" }}>{it.severity}</td>
                          <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{it.aiScore}</td>
                          <td style={{ padding: "6px 8px", color: "#166534", fontWeight: 700 }}>✓ Verified</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: "24px",
                    paddingTop: "16px",
                    borderTop: "1px solid #e2e8f0",
                    fontSize: "10px",
                  }}
                >
                  <div>
                    <strong>Surveyor:</strong> Alex Wong (Loss Adjuster #9921)
                  </div>
                  <div>
                    <strong>Status:</strong>{" "}
                    <span style={{ color: "#166534", fontWeight: 700 }}>LOCKED &amp; VERIFIED</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          MODAL 2: DIGITAL SURVEYOR SIGN-OFF
          ========================================================================= */}
      {isSignOffModalOpen && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0, 0, 0, 0.8)",
            backdropFilter: "blur(10px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 999,
          }}
          onClick={() => setIsSignOffModalOpen(false)}
        >
          <div
            style={{
              background: "var(--surface-card)",
              border: "1px solid var(--border-color)",
              borderRadius: "16px",
              width: "90%",
              maxWidth: "500px",
              padding: "24px",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ fontSize: 16, fontWeight: 800, marginBottom: 10 }}>Surveyor Digital Sign-Off</h3>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              By signing below, you certify that the damage items and photos for report{" "}
              <strong>{caseData.id}</strong> ({caseData.plate}) have been verified and comply with loss adjuster
              standards.
            </p>

            <div
              style={{
                border: "2px dashed var(--border-hover)",
                borderRadius: "8px",
                height: "110px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "var(--surface-elevated)",
                marginBottom: "16px",
              }}
            >
              <span style={{ fontFamily: "cursive", fontSize: "32px", color: "var(--accent-cyan)" }}>
                Alex Wong
              </span>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="btn-secondary-modern" onClick={() => setIsSignOffModalOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn-primary-modern"
                onClick={() => {
                  setIsSignOffModalOpen(false);
                  setIsSignedOff(true);
                  alert(`✓ Report ${caseData.id} (${caseData.plate}) has been officially Signed Off! Locked PDF generated.`);
                }}
              >
                <span>✍️</span> Confirm &amp; Lock PDF
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
