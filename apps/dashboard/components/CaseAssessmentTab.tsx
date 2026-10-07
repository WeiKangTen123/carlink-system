"use client";

import React, { useState, useRef, useEffect } from "react";
import {
  Wrench,
  DollarSign,
  ShieldCheck,
  Calendar,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Scale,
  Sparkles,
  FileCheck2,
  Stamp,
  Layers,
  Percent,
  Check,
  RefreshCw,
  Printer,
  FileText,
  BadgeAlert,
  ArrowDownRight,
  ShieldAlert,
} from "lucide-react";
import type {
  ReportDetail,
  DamageSummaryItem,
  AnnexAPartItem,
  AnnexBLabourItem,
  SignOffRequest,
} from "@/lib/api";
import { signOffReportAction } from "@/app/reports/actions";
import { CaseAnalysisPanel } from "./CaseAnalysisPanel";

export function CaseAssessmentTab({
  report,
  damageEntries,
}: {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
}) {
  const d = report.data;
  const v = d.vehicle_info;
  const ins = d.insurance_details;
  const signOff = d.sign_off;
  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";

  // Active Sub-Tab
  type SubTab = "annexA" | "annexB" | "annexC" | "bola";
  const [activeSubTab, setActiveSubTab] = useState<SubTab>("annexA");

  // Annex A: Spare Parts Items State
  const initialAnnexA: AnnexAPartItem[] =
    d.annex_a?.items && d.annex_a.items.length > 0
      ? d.annex_a.items
      : (damageEntries || []).map((dm, idx) => ({
          item_no: `${idx + 1}.0`,
          part_name: dm.part,
          condition: dm.damage_type || "Deformed/cut",
          action: "Replace",
          qty: 1.0,
          workshop_est: 850.0,
          discount_pct: 0.20,
          adjusted_cost: 680.0,
          is_net_item: false,
          oem_part_number: dm.oem_part_number || `71501-T7A-${idx + 1}00`,
        }));

  const [annexAItems, setAnnexAItems] = useState<AnnexAPartItem[]>(initialAnnexA);

  // Annex B: Labour Items State
  const initialAnnexB: AnnexBLabourItem[] =
    d.annex_b?.items && d.annex_b.items.length > 0
      ? d.annex_b.items
      : [
          { item_no: "1.0", description: "Towing charges from incident scene to workshop", workshop_est: 100.0, adjusted_cost: 60.0, justification: "Standard baseline towing tariff" },
          { item_no: "2.0", description: "To check electrical wiring system and lighting circuits", workshop_est: 80.0, adjusted_cost: 60.0, justification: "Benchmarked circuit testing rate" },
          { item_no: "3.0", description: "To tuff coat affected underbody chassis areas", workshop_est: 180.0, adjusted_cost: 120.0, justification: "Anti-corrosion chemical treatment" },
          { item_no: "4.0", description: "To remove & refix rear windscreen glass", workshop_est: 180.0, adjusted_cost: 120.0, justification: "Glass specialist tariff" },
          { item_no: "5.0", description: "To remove & replace reverse sensor unit", workshop_est: 120.0, adjusted_cost: 80.0, justification: "Sensor replacement labour" },
          { item_no: "6.0", description: "To adjust chassis alignment on Car-O-Liner frame jig", workshop_est: 380.0, adjusted_cost: 280.0, justification: "Structural measuring bench rate" },
          { item_no: "7.0", description: "To respray affected areas (2K Oven Baked - 5 panels)", workshop_est: 1800.0, adjusted_cost: 1250.0, justification: "5 Panels @ $250/panel benchmark" },
          { item_no: "8.0", description: "To renew damaged parts, straighten rear chassis members & align", workshop_est: 1800.0, adjusted_cost: 1500.0, justification: "Panel beating and assembly" },
          { item_no: "9.0", description: "To LTA remove and reseal vehicle registration number plate", workshop_est: 120.0, adjusted_cost: 100.0, justification: "Statutory inspection seal fee" },
        ];

  const [annexBItems, setAnnexBItems] = useState<AnnexBLabourItem[]>(initialAnnexB);

  // Annex C: Contract Lump Sum & SLA
  const initialLumpSum = d.annex_c?.agreed_lump_sum || 8700.0;
  const initialDays = d.annex_c?.repair_days || 11;
  const [contractLumpSum, setContractLumpSum] = useState<number>(initialLumpSum);
  const [repairTurnaroundDays, setRepairTurnaroundDays] = useState<number>(initialDays);

  // Financial Calculations
  const totalPartsClaimed = annexAItems.reduce((acc, it) => acc + (it.workshop_est * (it.qty || 1)), 0);
  const totalPartsAdjusted = annexAItems.reduce((acc, it) => acc + it.adjusted_cost, 0);

  const totalLabourClaimed = annexBItems.reduce((acc, it) => acc + it.workshop_est, 0);
  const totalLabourAdjusted = annexBItems.reduce((acc, it) => acc + it.adjusted_cost, 0);

  const grossClaimedTotal = totalPartsClaimed + totalLabourClaimed;
  const grossAdjustedTotal = totalPartsAdjusted + totalLabourAdjusted;

  const currentLumpSum = contractLumpSum || Math.round(grossAdjustedTotal * 0.85);
  const gstAmount = Math.round(currentLumpSum * 0.09 * 100) / 100;
  const grandTotalWithGst = Math.round((currentLumpSum + gstAmount) * 100) / 100;

  const totalSavingsAchieved = grossClaimedTotal - currentLumpSum;
  const savingsPct = grossClaimedTotal > 0 ? ((totalSavingsAchieved / grossClaimedTotal) * 100).toFixed(1) : "0.0";

  // Handlers for Annex A Editing
  const handlePartDiscountChange = (index: number, newDiscount: number) => {
    if (isSignedOff) return;
    setAnnexAItems((prev) => {
      const copy = [...prev];
      const item = { ...copy[index] };
      item.discount_pct = newDiscount;
      item.adjusted_cost = Math.round(item.qty * item.workshop_est * (1.0 - newDiscount) * 100) / 100;
      copy[index] = item;
      return copy;
    });
  };

  const handlePartActionChange = (index: number, newAction: string) => {
    if (isSignedOff) return;
    setAnnexAItems((prev) => {
      const copy = [...prev];
      const item = { ...copy[index] };
      item.action = newAction;
      if (newAction === "Disallow") {
        item.discount_pct = 1.0;
        item.adjusted_cost = 0.0;
      } else if (item.discount_pct === 1.0) {
        item.discount_pct = 0.20;
        item.adjusted_cost = Math.round(item.qty * item.workshop_est * 0.80 * 100) / 100;
      }
      copy[index] = item;
      return copy;
    });
  };

  const handleApplyTradeDiscountToAll = () => {
    if (isSignedOff) return;
    setAnnexAItems((prev) =>
      prev.map((it) => {
        if (it.is_net_item || it.action === "Disallow") return it;
        return {
          ...it,
          discount_pct: 0.20,
          adjusted_cost: Math.round(it.qty * it.workshop_est * 0.80 * 100) / 100,
        };
      })
    );
  };

  // Sign-Off & Endorsement State
  const [surveyorName, setSurveyorName] = useState(signOff?.surveyor_name || "Patrick Ng");
  const [qualifications, setQualifications] = useState(signOff?.qualifications || "MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC");
  const [licenseNumber, setLicenseNumber] = useState(signOff?.license_number || "SURV-SG-0492");
  const [signatureMode, setSignatureMode] = useState<"seal" | "canvas">("seal");
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  const [declarations, setDeclarations] = useState({
    mcf_inspection: true,
    damage_scope: true,
    market_pricing: true,
    without_prejudice: true,
  });

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);

  useEffect(() => {
    if (canvasRef.current && signatureMode === "canvas") {
      const ctx = canvasRef.current.getContext("2d");
      if (ctx) {
        ctx.strokeStyle = "#38bdf8";
        ctx.lineWidth = 2.5;
        ctx.lineCap = "round";
      }
    }
  }, [signatureMode]);

  const handleClearSignature = () => {
    if (canvasRef.current) {
      const ctx = canvasRef.current.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
    }
  };

  const handleSignOffSubmit = async () => {
    if (!declarations.mcf_inspection || !declarations.damage_scope || !declarations.market_pricing || !declarations.without_prejudice) {
      alert("Please confirm all 4 statutory declarations before signing off.");
      return;
    }

    setIsSigningOff(true);
    setSignOffError(null);

    let sigDataUrl = null;
    if (signatureMode === "canvas" && canvasRef.current) {
      sigDataUrl = canvasRef.current.toDataURL("image/png");
    }

    const payload: SignOffRequest = {
      surveyor_name: surveyorName,
      qualifications,
      license_number: licenseNumber,
      firm_name: "Carlink Consultancy",
      agreed_quantum: currentLumpSum,
      turnaround_days: repairTurnaroundDays,
      liability_opinion: "100% Third Party Liability under GIA BOLA Scenario #14",
      signature_data_url: sigDataUrl || undefined,
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

  return (
    <div className="assessment-workstation">
      {/* =========================================================================
          TIER 1: EXECUTIVE QUANTUM RIBBON & WORKSHOP REPAIR SLA
          ========================================================================= */}
      <div className="quantum-ribbon">
        <div className="quantum-kpi-card">
          <div className="quantum-kpi-title">
            <DollarSign style={{ width: 14, height: 14, color: "var(--text-muted)" }} /> Workshop Claimed (Gross)
          </div>
          <div className="quantum-kpi-value" style={{ color: "var(--text-secondary)" }}>
            S${grossClaimedTotal.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="quantum-kpi-sub">
            {annexAItems.length} Parts &bull; {annexBItems.length} Labour Operations
          </div>
        </div>

        <div className="quantum-kpi-card">
          <div className="quantum-kpi-title">
            <Scale style={{ width: 14, height: 14, color: "var(--accent-primary)" }} /> Adjuster Assessed Quantum
          </div>
          <div className="quantum-kpi-value" style={{ color: "var(--accent-cyan)" }}>
            S${grossAdjustedTotal.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="quantum-kpi-sub">
            Part-by-Part Technical Audit Sum
          </div>
        </div>

        <div className="quantum-kpi-card" style={{ borderColor: "var(--accent-cyan)", background: "rgba(56, 189, 248, 0.05)" }}>
          <div className="quantum-kpi-title">
            <Sparkles style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} /> Agreed Contract Lump Sum
          </div>
          <div className="quantum-kpi-value" style={{ color: "#ffffff" }}>
            S${currentLumpSum.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="quantum-kpi-sub" style={{ color: "var(--accent-cyan)", fontWeight: 700 }}>
            +9% GST: S${grandTotalWithGst.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
        </div>

        <div className="quantum-kpi-card" style={{ borderColor: "rgba(16, 185, 129, 0.3)", background: "rgba(16, 185, 129, 0.05)" }}>
          <div className="quantum-kpi-title">
            <ArrowDownRight style={{ width: 14, height: 14, color: "#10b981" }} /> Insurer Savings Achieved
          </div>
          <div className="quantum-kpi-value" style={{ color: "#10b981" }}>
            -S${totalSavingsAchieved.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="quantum-kpi-sub" style={{ color: "#10b981", fontWeight: 700 }}>
            {savingsPct}% Net Reduction for Insurer
          </div>
        </div>

        <div className="quantum-kpi-card">
          <div className="quantum-kpi-title">
            <Clock style={{ width: 14, height: 14, color: "#f59e0b" }} /> Repair Cycle Time SLA
          </div>
          <div className="quantum-kpi-value" style={{ color: "#f59e0b" }}>
            {repairTurnaroundDays} Working Days
          </div>
          <div className="quantum-kpi-sub">
            ATP: <span style={{ color: "#10b981", fontWeight: 700 }}>{isSignedOff ? "GRANTED & SEALED" : "PENDING ENDORSEMENT"}</span>
          </div>
        </div>
      </div>

      {/* Case Analysis Overview Bar */}
      <CaseAnalysisPanel report={report} damageEntries={damageEntries} />

      {/* =========================================================================
          TIER 2: TABBED MULTI-ANNEX QUANTUM LEDGERS
          ========================================================================= */}
      <div className="card-glass" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "14px 20px", borderBottom: "1px solid var(--border-color)", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div className="annex-nav-bar" style={{ borderBottom: "none", marginBottom: 0 }}>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "annexA" ? "active" : ""}`}
              onClick={() => setActiveSubTab("annexA")}
            >
              <Layers style={{ width: 14, height: 14 }} /> Annex A: Spare Parts Schedule ({annexAItems.length})
            </button>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "annexB" ? "active" : ""}`}
              onClick={() => setActiveSubTab("annexB")}
            >
              <Wrench style={{ width: 14, height: 14 }} /> Annex B: Labour &amp; Refinishing ({annexBItems.length})
            </button>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "annexC" ? "active" : ""}`}
              onClick={() => setActiveSubTab("annexC")}
            >
              <Percent style={{ width: 14, height: 14 }} /> Annex C: Settlement &amp; Waterfall
            </button>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "bola" ? "active" : ""}`}
              onClick={() => setActiveSubTab("bola")}
            >
              <Scale style={{ width: 14, height: 14 }} /> Causation &amp; BOLA Liability
            </button>
          </div>

          {!isSignedOff && activeSubTab === "annexA" && (
            <button
              type="button"
              className="btn-secondary-modern"
              style={{ fontSize: 11, padding: "5px 12px" }}
              onClick={handleApplyTradeDiscountToAll}
            >
              <Sparkles style={{ width: 13, height: 13, color: "var(--accent-cyan)" }} /> Apply Standard 20% Trade Discount
            </button>
          )}
        </div>

        {/* SUB-TAB 1: ANNEX A (SPARE PARTS LEDGER) */}
        {activeSubTab === "annexA" && (
          <div style={{ padding: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
              <div>
                <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex A : Assessment / Adjustment on Spare Parts</h4>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  Standard GIA 20% trade discount applied to List Items. Serviceable components 100% disallowed.
                </div>
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span className="chip-severity minor" style={{ fontSize: 11 }}>
                  List Items: {annexAItems.filter((i) => !i.is_net_item).length}
                </span>
                <span className="chip-severity minor" style={{ fontSize: 11 }}>
                  S/Nett Items: {annexAItems.filter((i) => i.is_net_item).length}
                </span>
              </div>
            </div>

            <div className="annex-table-wrapper">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th style={{ width: 45 }}>Item</th>
                    <th>Vehicle Parts Description</th>
                    <th>Condition</th>
                    <th>Action</th>
                    <th style={{ textAlign: "center", width: 50 }}>Qty</th>
                    <th style={{ textAlign: "right", width: 110 }}>W/Shop Est. ($)</th>
                    <th style={{ textAlign: "center", width: 90 }}>Disc (%)</th>
                    <th style={{ textAlign: "right", width: 110 }}>Adjusted ($)</th>
                    <th>OEM Part #</th>
                  </tr>
                </thead>
                <tbody>
                  {annexAItems.map((item, idx) => {
                    const isDisallowed = item.action === "Disallow" || item.discount_pct >= 0.99;
                    return (
                      <tr key={idx} style={{ opacity: isDisallowed ? 0.65 : 1 }}>
                        <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{item.item_no || `${idx + 1}.0`}</td>
                        <td style={{ fontWeight: 600 }}>{item.part_name}</td>
                        <td>
                          <span className={`annex-action-badge ${item.condition === "Serviceable" ? "disallow" : item.condition.includes("Deformed") ? "replace" : "repair"}`}>
                            {item.condition}
                          </span>
                        </td>
                        <td>
                          {isSignedOff ? (
                            <span className={`annex-action-badge ${item.action === "Disallow" ? "disallow" : item.action === "Replace" ? "replace" : "repair"}`}>
                              {item.action}
                            </span>
                          ) : (
                            <select
                              value={item.action}
                              onChange={(e) => handlePartActionChange(idx, e.target.value)}
                              style={{
                                background: "var(--surface-base)",
                                color: "var(--text-primary)",
                                border: "1px solid var(--border-color)",
                                borderRadius: 4,
                                padding: "2px 6px",
                                fontSize: 11,
                              }}
                            >
                              <option value="Replace">Replace</option>
                              <option value="Repair">Repair</option>
                              <option value="Straighten">Straighten</option>
                              <option value="Disallow">Disallow</option>
                            </select>
                          )}
                        </td>
                        <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>{item.qty || 1}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                          S${(item.workshop_est * (item.qty || 1)).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ textAlign: "center", fontFamily: "var(--font-mono)" }}>
                          {item.is_net_item ? (
                            <span style={{ color: "var(--text-muted)", fontSize: 10 }}>S/Nett (0%)</span>
                          ) : isSignedOff ? (
                            <span>{Math.round(item.discount_pct * 100)}%</span>
                          ) : (
                            <select
                              value={item.discount_pct}
                              onChange={(e) => handlePartDiscountChange(idx, parseFloat(e.target.value))}
                              style={{
                                background: "var(--surface-base)",
                                color: "var(--text-primary)",
                                border: "1px solid var(--border-color)",
                                borderRadius: 4,
                                padding: "2px 4px",
                                fontSize: 11,
                              }}
                            >
                              <option value={0.0}>0% (Nett)</option>
                              <option value={0.10}>10%</option>
                              <option value={0.15}>15%</option>
                              <option value={0.20}>20% (GIA)</option>
                              <option value={0.30}>30%</option>
                              <option value={1.0}>100% (Disallow)</option>
                            </select>
                          )}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: isDisallowed ? "var(--text-muted)" : "var(--accent-cyan)" }}>
                          S${item.adjusted_cost.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
                          {item.oem_part_number || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={5} style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      Total For Spare Parts (Annex A)
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>
                      S${totalPartsClaimed.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td style={{ textAlign: "center", color: "#10b981", fontSize: 11 }}>
                      -S${(totalPartsClaimed - totalPartsAdjusted).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>
                      S${totalPartsAdjusted.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        )}

        {/* SUB-TAB 2: ANNEX B (LABOUR & REFINISHING LEDGER) */}
        {activeSubTab === "annexB" && (
          <div style={{ padding: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
              <div>
                <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex B : Adjustment on Labour and Spray Painting</h4>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                  Benchmarked against standard Singapore automotive loss adjusting tariffs and 2K oven baked refinishing schedule.
                </div>
              </div>
            </div>

            <div className="annex-table-wrapper">
              <table className="annex-table">
                <thead>
                  <tr>
                    <th style={{ width: 45 }}>Item</th>
                    <th>Job Description</th>
                    <th style={{ textAlign: "right", width: 130 }}>Workshop Est. ($)</th>
                    <th style={{ textAlign: "right", width: 130 }}>Adjusted Cost ($)</th>
                    <th>Adjuster Technical Justification</th>
                  </tr>
                </thead>
                <tbody>
                  {annexBItems.map((lab, idx) => (
                    <tr key={idx}>
                      <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{lab.item_no || `${idx + 1}.0`}</td>
                      <td style={{ fontWeight: 600 }}>{lab.description}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                        S${lab.workshop_est.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)" }}>
                        S${lab.adjusted_cost.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {lab.justification || "Benchmarked tariff applied"}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2} style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      Total For Labour &amp; Spray Painting (Annex B)
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>
                      S${totalLabourClaimed.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>
                      S${totalLabourAdjusted.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td style={{ color: "#10b981", fontSize: 11 }}>
                      Variance: -S${(totalLabourClaimed - totalLabourAdjusted).toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        )}

        {/* SUB-TAB 3: ANNEX C (SETTLEMENT WATERFALL & 3-WAY RECONCILIATION) */}
        {activeSubTab === "annexC" && (
          <div style={{ padding: 20 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 0.8fr", gap: 20 }}>
              <div className="waterfall-card">
                <div style={{ display: "flex", alignItems: "center", gap: 8, borderBottom: "1px solid var(--border-color)", paddingBottom: 10 }}>
                  <Scale style={{ width: 16, height: 16, color: "var(--accent-primary)" }} />
                  <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex C : 3-Way Quantum Reconciliation</h4>
                </div>

                <div className="waterfall-row">
                  <span className="waterfall-label">1. Annex A: Spare Parts Total</span>
                  <div style={{ display: "flex", gap: 20 }}>
                    <span style={{ color: "var(--text-muted)" }}>Claimed: S${totalPartsClaimed.toFixed(2)}</span>
                    <span className="waterfall-val">S${totalPartsAdjusted.toFixed(2)}</span>
                  </div>
                </div>

                <div className="waterfall-row">
                  <span className="waterfall-label">2. Annex B: Labour &amp; Spray Painting Total</span>
                  <div style={{ display: "flex", gap: 20 }}>
                    <span style={{ color: "var(--text-muted)" }}>Claimed: S${totalLabourClaimed.toFixed(2)}</span>
                    <span className="waterfall-val">S${totalLabourAdjusted.toFixed(2)}</span>
                  </div>
                </div>

                <div className="waterfall-row subtotal">
                  <span>3. Total Repair Costs (Part-by-Part Adjusted)</span>
                  <div style={{ display: "flex", gap: 20 }}>
                    <span style={{ color: "var(--text-muted)", textDecoration: "line-through" }}>S${grossClaimedTotal.toFixed(2)}</span>
                    <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>S${grossAdjustedTotal.toFixed(2)}</span>
                  </div>
                </div>

                <div className="waterfall-row" style={{ background: "rgba(56, 189, 248, 0.05)", padding: "10px 14px", borderRadius: 6 }}>
                  <div>
                    <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>4. Recommended Contract Lump Sum Settlement</div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Negotiated commercial concession with repairer discretion</div>
                  </div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, fontWeight: 800, color: "var(--accent-cyan)" }}>
                    S${currentLumpSum.toFixed(2)}
                  </div>
                </div>

                <div className="waterfall-row">
                  <span className="waterfall-label">5. Prevailing Singapore GST (9%)</span>
                  <span className="waterfall-val">S${gstAmount.toFixed(2)}</span>
                </div>

                <div className="waterfall-row grand">
                  <div>
                    <div>Grand Total Payable (Including 9% GST)</div>
                    <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.8 }}>Full &amp; final discharge on contract lump sum basis</div>
                  </div>
                  <div style={{ fontSize: 20, fontFamily: "var(--font-mono)" }}>
                    S${grandTotalWithGst.toFixed(2)}
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 8, fontSize: 11 }}>
                  <span style={{ color: "#10b981", fontWeight: 700 }}>Total Claim Reduction Achieved:</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "#10b981", fontWeight: 800 }}>
                    S${totalSavingsAchieved.toFixed(2)} ({savingsPct}%)
                  </span>
                </div>
              </div>

              {/* Settlement Terms Card */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="card-glass" style={{ height: "100%" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <FileCheck2 style={{ width: 16, height: 16, color: "var(--accent-cyan)" }} />
                    <h4 style={{ fontSize: 13, fontWeight: 700, margin: 0 }}>Statutory Settlement Terms</h4>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    <p style={{ margin: 0 }}>
                      <strong>Repairer Discretion:</strong> The repairer has agreed to undertake the repairs on a contract lump sum of{" "}
                      <strong style={{ color: "var(--text-primary)" }}>S${currentLumpSum.toFixed(2)}</strong>, according to acceptable quality and standard (Repairer discretion to repair parts, replace with reconditioned/used parts, or replace with OEM/genuine parts).
                    </p>
                    <p style={{ margin: 0 }}>
                      <strong>Repair Turnaround Period:</strong> The entire repair of the damaged vehicle should be completed within a reasonable period of{" "}
                      <strong style={{ color: "#f59e0b" }}>{repairTurnaroundDays} working days</strong> to mitigate consequential loss of use claims.
                    </p>
                  </div>

                  <div className="legal-without-prejudice-banner" style={{ marginTop: 16 }}>
                    <div className="legal-banner-title">
                      <ShieldAlert style={{ width: 14, height: 14 }} /> STRICTLY WITHOUT PREJUDICE
                    </div>
                    <p className="legal-banner-text" style={{ fontSize: 11 }}>
                      This inspection was conducted strictly on a WITHOUT PREJUDICE basis. We have not given any instruction and authorisation to proceed with the repair of the vehicle. Matter reverted for principal discretion.
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* SUB-TAB 4: CAUSATION & BOLA LIABILITY FINDING */}
        {activeSubTab === "bola" && (
          <div style={{ padding: 20 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
              <div className="card-glass">
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                  <ShieldCheck style={{ width: 16, height: 16, color: "#10b981" }} />
                  <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Forensic Causation Consistency</h4>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 12, fontSize: 12 }}>
                  <div>
                    <div className="detail-field-label">Point of Impact</div>
                    <div style={{ fontWeight: 700, marginTop: 2 }}>{v?.point_of_impact || "Rear centre & rear bumper"}</div>
                  </div>
                  <div>
                    <div className="detail-field-label">Impact Height &amp; Direction of Force</div>
                    <div style={{ marginTop: 2, color: "var(--text-secondary)" }}>
                      Contact zone: 380mm – 860mm from ground. Longitudinal forward compressive shockwave Crumpled rear end panel and corrugated boot floor panel.
                    </div>
                  </div>
                  <div>
                    <div className="detail-field-label">Narrative Consistency Finding</div>
                    <div style={{ marginTop: 2, color: "#10b981", fontWeight: 700 }}>
                      ✓ 100% CONSISTENT with reported collision events
                    </div>
                  </div>
                  <div>
                    <div className="detail-field-label">Pre-Existing / Wear Exclusions</div>
                    <div style={{ marginTop: 2, color: "var(--text-secondary)" }}>
                      Tailgate hinges noted as serviceable (excluded from claim). No prior unrectified structural crumple detected.
                    </div>
                  </div>
                </div>
              </div>

              {/* BOLA Matrix Card */}
              <div className="card-glass" style={{ border: "1px solid var(--accent-cyan)", background: "rgba(56, 189, 248, 0.03)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Scale style={{ width: 16, height: 16, color: "var(--accent-cyan)" }} />
                    <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>GIA BOLA Liability Apportionment</h4>
                  </div>
                  <span className="badge-plate-glow" style={{ fontSize: 10, padding: "2px 8px" }}>
                    RULE #14
                  </span>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
                      BOLA Scenario #14: Direct Rear-End Collision
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      Leading vehicle stationary or slowing down; following vehicle fails to maintain safe stopping clearance.
                    </div>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div style={{ background: "rgba(16, 185, 129, 0.1)", padding: "10px 12px", borderRadius: 8, border: "1px solid rgba(16, 185, 129, 0.3)" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "#10b981", textTransform: "uppercase" }}>Insured Liability</div>
                      <div style={{ fontSize: 20, fontWeight: 800, fontFamily: "var(--font-mono)", color: "#10b981" }}>0%</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Zero contributory fault</div>
                    </div>

                    <div style={{ background: "rgba(239, 68, 68, 0.1)", padding: "10px 12px", borderRadius: 8, border: "1px solid rgba(239, 68, 68, 0.3)" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "#ef4444", textTransform: "uppercase" }}>Third Party Liability</div>
                      <div style={{ fontSize: 20, fontWeight: 800, fontFamily: "var(--font-mono)", color: "#ef4444" }}>100%</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>Sole tortfeasor fault</div>
                    </div>
                  </div>

                  <div>
                    <div className="detail-field-label">Subrogation Recovery Prospect</div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>
                      100% Third-Party Property Damage (TPPD) Subrogation Recommended
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      Target Insurer: {ins?.insurer_name || "Tokio Marine Singapore"} &bull; Counterpart Ref: {ins?.claim_number || "CL 11900"}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* =========================================================================
          TIER 3: PROFESSIONAL LOSS ADJUSTER SIGN-OFF & ENDORSEMENT CONSOLE
          ========================================================================= */}
      <div className="endorsement-console">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Stamp style={{ width: 18, height: 18, color: "var(--accent-cyan)" }} />
              <h3 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>Loss Adjuster Statutory Endorsement &amp; Certification</h3>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
              Independent Automotive Engineer Assessor Certification under Singapore GIA Framework.
            </div>
          </div>

          {isSignedOff ? (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                background: "var(--badge-green-bg)",
                color: "var(--badge-green-text)",
                border: "1px solid var(--badge-green-border)",
                padding: "6px 14px",
                borderRadius: 8,
                fontWeight: 700,
                fontSize: 12,
              }}
            >
              <ShieldCheck style={{ width: 16, height: 16 }} /> CERTIFIED &amp; LOCKED DOSSIER
            </span>
          ) : (
            <span className="chip-severity moderate" style={{ fontSize: 11 }}>
              PENDING SURVEYOR ENDORSEMENT
            </span>
          )}
        </div>

        {/* Assessor Credentials Banner */}
        <div className="surveyor-credentials-strip">
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)" }}>Appointed Assessor</div>
            <div style={{ fontSize: 14, fontWeight: 800, color: "var(--text-primary)" }}>{surveyorName}</div>
            <div style={{ fontSize: 11, color: "var(--accent-cyan)", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{qualifications}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)" }}>License Registration</div>
            <div style={{ fontSize: 12, fontWeight: 700, fontFamily: "var(--font-mono)" }}>{licenseNumber}</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Carlink Survey Services Pte Ltd</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)" }}>Firm Registry</div>
            <div style={{ fontSize: 12, fontWeight: 700 }}>60 Hillside Drive, S549009</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Tel: 6285 6178 / Fax: 6287 4788</div>
          </div>
        </div>

        {/* 4 Mandatory Statutory Declarations */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
            Mandatory Compliance Attestations
          </div>

          <label className="compliance-check-item">
            <input
              type="checkbox"
              checked={declarations.mcf_inspection}
              disabled={isSignedOff}
              onChange={(e) => setDeclarations((prev) => ({ ...prev, mcf_inspection: e.target.checked }))}
            />
            <span>1. Physical inspection conducted and photographic evidence verified in accordance with the Singapore GIA Motor Claims Framework.</span>
          </label>

          <label className="compliance-check-item">
            <input
              type="checkbox"
              checked={declarations.damage_scope}
              disabled={isSignedOff}
              onChange={(e) => setDeclarations((prev) => ({ ...prev, damage_scope: e.target.checked }))}
            />
            <span>2. Damage scope scrutinized; all non-accident, wear-and-tear, or pre-existing damages have been rigorously excluded from assessment.</span>
          </label>

          <label className="compliance-check-item">
            <input
              type="checkbox"
              checked={declarations.market_pricing}
              disabled={isSignedOff}
              onChange={(e) => setDeclarations((prev) => ({ ...prev, market_pricing: e.target.checked }))}
            />
            <span>3. Parts pricing vetted against prevailing OEM/OES market tariffs with standard 20% trade discounts applied to List Items.</span>
          </label>

          <label className="compliance-check-item">
            <input
              type="checkbox"
              checked={declarations.without_prejudice}
              disabled={isSignedOff}
              onChange={(e) => setDeclarations((prev) => ({ ...prev, without_prejudice: e.target.checked }))}
            />
            <span>4. Certified assessment represents fair, reasonable, and necessary repair costs strictly on a WITHOUT PREJUDICE basis.</span>
          </label>
        </div>

        {/* Digital Signature & Stamp Box */}
        <div className="surveyor-signature-box">
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)" }}>
                Surveyor Endorsement Signature
              </div>
              {!isSignedOff && (
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    style={{ fontSize: 10, background: "none", border: "none", color: "var(--accent-cyan)", cursor: "pointer", textDecoration: "underline" }}
                    onClick={() => setSignatureMode(signatureMode === "seal" ? "canvas" : "seal")}
                  >
                    {signatureMode === "seal" ? "Draw Signature" : "Use Digital Seal"}
                  </button>
                  {signatureMode === "canvas" && (
                    <button
                      type="button"
                      style={{ fontSize: 10, background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer" }}
                      onClick={handleClearSignature}
                    >
                      Clear
                    </button>
                  )}
                </div>
              )}
            </div>

            {signatureMode === "canvas" && !isSignedOff ? (
              <canvas
                ref={canvasRef}
                width={360}
                height={160}
                className="signature-canvas-pad"
                onMouseDown={() => setIsDrawing(true)}
                onMouseUp={() => setIsDrawing(false)}
                onMouseMove={(e) => {
                  if (!isDrawing || !canvasRef.current) return;
                  const ctx = canvasRef.current.getContext("2d");
                  if (!ctx) return;
                  const rect = canvasRef.current.getBoundingClientRect();
                  ctx.lineTo(e.clientX - rect.left, e.clientY - rect.top);
                  ctx.stroke();
                }}
              />
            ) : (
              <div className="signature-canvas-pad" style={{ background: "rgba(56, 189, 248, 0.02)" }}>
                <div style={{ fontFamily: "Georgia, serif", fontStyle: "italic", fontSize: 24, color: "var(--accent-cyan)", letterSpacing: "0.05em" }}>
                  {surveyorName}
                </div>
                <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                  Automotive Engineer Assessor &bull; Reg: {licenseNumber}
                </div>
                {signOff?.signature_date && (
                  <div style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 4 }}>
                    Endorsed on: {new Date(signOff.signature_date).toLocaleString("en-SG")}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Official Company Seal Preview */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 6 }}>
              Official Assessor Chop &amp; Seal
            </div>
            <div className="surveyor-chop-preview">
              <div style={{ fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-primary)" }}>
                CARLINK CONSULTANCY
              </div>
              <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
                60 Hillside Drive, Singapore 549009
              </div>
              <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent-primary)", marginTop: 4 }}>
                PATRICK NG &bull; AUTOMOTIVE ENGINEER ASSESSOR
              </div>
              <div style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                MIMI, MIRTE, LCGI, I ENG, LAE, CGLI FTC
              </div>
              <div style={{ fontSize: 8, fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", marginTop: 4 }}>
                SHA-256 SEAL: {signOff?.signature_hash || "9F8A3B12D04C5E76F891A234BCDE0921"}
              </div>
            </div>
          </div>
        </div>

        {/* Action Button Strip */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10, borderTop: "1px solid var(--border-color)", flexWrap: "wrap", gap: 10 }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {isSignedOff
              ? "This case is locked. To modify line items, reopen the report from the edit tab."
              : "Signing off finalizes Annex A, B, and C, embeds the digital seal into the PDF, and locks the case."}
          </div>

          {signOffError && (
            <div style={{ color: "var(--danger, #ef4444)", fontSize: 12 }}>
              {signOffError}
            </div>
          )}

          {!isSignedOff ? (
            <button
              type="button"
              className="btn-primary-modern"
              style={{ padding: "10px 24px", fontSize: 13 }}
              disabled={isSigningOff}
              onClick={handleSignOffSubmit}
            >
              <CheckCircle2 style={{ width: 16, height: 16 }} />
              {isSigningOff ? "Finalizing & Sealing Dossier..." : "Finalize Assessment & Endorse (Lock Dossier)"}
            </button>
          ) : (
            <a href={`/reports/${report.id}/download`} className="btn-secondary-modern">
              <Printer style={{ width: 14, height: 14 }} /> Download Certified PDF Dossier
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
