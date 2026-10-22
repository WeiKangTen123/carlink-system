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
import {
  type ReportDetail,
  type DamageSummaryItem,
  type AnnexAPartItem,
  type AnnexBLabourItem,
  type SignOffRequest,
  pdfDownloadUrl,
} from "@/lib/api";
import { signOffReportAction } from "@/app/reports/actions";
import { useCurrentUser } from "@/components/CurrentUser";
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

  // Annex A: Spare Parts Items State. The recorded rows only -- with none,
  // this used to invent a row per damaged part (S$850 workshop estimate, 20%
  // off to S$680, condition "Deformed/cut", OEM no. "71501-T7A-n00") that
  // fed every total on this tab.
  //
  // Edits to Annex A rows on this tab (action / discount; Annex B has no
  // edit controls) are LOCAL ONLY: SignOffRequest carries no line items and
  // nothing else saves them, so they are a working preview that is lost on
  // reload. The UI says so.
  const initialAnnexA: AnnexAPartItem[] = d.annex_a?.items || [];

  const [annexAItems, setAnnexAItems] = useState<AnnexAPartItem[]>(initialAnnexA);

  // Annex B: Labour Items State. Recorded rows only (with none, this used to
  // fall back to nine labour lines copied from the SLK case).
  const initialAnnexB: AnnexBLabourItem[] = d.annex_b?.items || [];

  const [annexBItems, setAnnexBItems] = useState<AnnexBLabourItem[]>(initialAnnexB);

  // Annex C: Contract Lump Sum & SLA -- empty until recorded or entered in
  // the sign-off console. They used to default to S$8,700 and 11 days,
  // which sign-off then saved as the case's agreed quantum.
  const [contractLumpSum, setContractLumpSum] = useState<number | null>(d.annex_c?.agreed_lump_sum ?? null);
  const [repairTurnaroundDays, setRepairTurnaroundDays] = useState<number | null>(d.annex_c?.repair_days ?? null);

  // Financial Calculations
  const totalPartsClaimed = annexAItems.reduce((acc, it) => acc + (it.workshop_est * (it.qty || 1)), 0);
  const totalPartsAdjusted = annexAItems.reduce((acc, it) => acc + it.adjusted_cost, 0);

  const totalLabourClaimed = annexBItems.reduce((acc, it) => acc + it.workshop_est, 0);
  const totalLabourAdjusted = annexBItems.reduce((acc, it) => acc + it.adjusted_cost, 0);

  const grossClaimedTotal = totalPartsClaimed + totalLabourClaimed;
  const grossAdjustedTotal = totalPartsAdjusted + totalLabourAdjusted;

  // Unpriced placeholder rows carry 0 -- a total over them is "—", not S$0.00.
  const hasPricedParts = annexAItems.some((it) => it.workshop_est > 0);
  const hasPricedLabour = annexBItems.some((it) => it.workshop_est > 0);
  const hasPricedItems = hasPricedParts || hasPricedLabour;

  const currentLumpSum = contractLumpSum;
  const gstAmount = currentLumpSum != null ? Math.round(currentLumpSum * 0.09 * 100) / 100 : null;
  const grandTotalWithGst = currentLumpSum != null && gstAmount != null ? Math.round((currentLumpSum + gstAmount) * 100) / 100 : null;

  // Reduction against the workshop's priced claim, only when both exist (it
  // was "Insurer Savings Achieved" off the invented figures above).
  const claimReduction = hasPricedItems && currentLumpSum != null ? grossClaimedTotal - currentLumpSum : null;
  const reductionPct = claimReduction != null && grossClaimedTotal > 0 ? ((claimReduction / grossClaimedTotal) * 100).toFixed(1) : null;

  const sgd = (n: number | null | undefined) =>
    n == null ? "—" : `S$${n.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  // GIA BOLA apportionment as recorded; null fields mean the surveyor hasn't
  // assessed it yet.
  const bola = d.bola_assessment;
  const bolaScenario = bola?.scenario_number ?? null;
  const insuredPct = bola?.insured_liability_pct ?? null;
  const tpPct = bola?.third_party_liability_pct ?? null;

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

  // Sign-Off & Endorsement State. Who signs is the signed-in user, with
  // their own profile (My account) -- the API takes it from the account.
  // This defaulted to one fixed surveyor's name, qualifications and
  // licence, so every case read as signed by them.
  const user = useCurrentUser();
  const signer = isSignedOff
    ? { name: signOff?.surveyor_name, qualifications: signOff?.qualifications, license: signOff?.license_number, firm: signOff?.firm_name }
    : { name: user?.name, qualifications: user?.qualifications, license: user?.license_number, firm: user?.firm_name };
  const surveyorName = signer.name || "—";
  const qualifications = signer.qualifications || "";
  const licenseNumber = signer.license || "—";
  const firmName = signer.firm || "";
  const profileIncomplete = !isSignedOff && Boolean(user) && !user?.license_number;
  const [signatureMode, setSignatureMode] = useState<"seal" | "canvas">("seal");
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  // The four attestations start unticked -- the surveyor makes them (they
  // used to start ticked). A signed-off case shows what was accepted.
  const attested = isSignedOff && signOff?.terms_accepted === true;
  const [declarations, setDeclarations] = useState({
    mcf_inspection: attested,
    damage_scope: attested,
    market_pricing: attested,
    without_prejudice: attested,
  });
  const allDeclared =
    declarations.mcf_inspection && declarations.damage_scope && declarations.market_pricing && declarations.without_prejudice;

  // From the recorded BOLA assessment or the surveyor's own entry; this used
  // to send "100% Third Party Liability under GIA BOLA Scenario #14" on
  // every sign-off.
  const [liabilityOpinion, setLiabilityOpinion] = useState<string>(
    isSignedOff && signOff?.liability_opinion
      ? signOff.liability_opinion
      : bolaScenario != null && tpPct != null
        ? `${tpPct}% Third Party Liability under GIA BOLA Scenario #${bolaScenario}`
        : bola?.apportionment_rationale || ""
  );

  // Placeholder the old annex synthesizer stored as a "signature hash";
  // never shown as a real seal.
  const PLACEHOLDER_SEAL_HASH = "9F8A3B12D04C5E76F891A234BCDE0921";
  const sealHash = signOff?.signature_hash && signOff.signature_hash !== PLACEHOLDER_SEAL_HASH ? signOff.signature_hash : null;

  const fieldInputStyle: React.CSSProperties = {
    background: "var(--surface-base)",
    color: "var(--text-primary)",
    border: "1px solid var(--border-color)",
    borderRadius: 4,
    padding: "6px 8px",
    fontSize: 12,
  };

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
    if (!allDeclared) {
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
      // Only what's recorded or entered; anything left empty stays empty.
      agreed_quantum: currentLumpSum ?? undefined,
      turnaround_days: repairTurnaroundDays ?? undefined,
      liability_opinion: liabilityOpinion.trim() || undefined,
      signature_data_url: sigDataUrl || undefined,
      // True only because the surveyor ticked all four (checked above).
      terms_accepted: allDeclared,
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
            {hasPricedItems ? sgd(grossClaimedTotal) : "—"}
          </div>
          <div className="quantum-kpi-sub">
            {annexAItems.length} Parts • {annexBItems.length} Labour Operations
          </div>
        </div>

        <div className="quantum-kpi-card">
          <div className="quantum-kpi-title">
            <Scale style={{ width: 14, height: 14, color: "var(--accent-primary)" }} /> Adjusted Quantum (Annex A + B)
          </div>
          <div className="quantum-kpi-value" style={{ color: "var(--accent-cyan)" }}>
            {hasPricedItems ? sgd(grossAdjustedTotal) : "—"}
          </div>
          <div className="quantum-kpi-sub">
            {hasPricedItems ? "Sum of adjusted line items" : "No priced line items yet"}
          </div>
        </div>

        <div className="quantum-kpi-card" style={{ borderColor: "var(--accent-cyan)", background: "rgba(56, 189, 248, 0.05)" }}>
          <div className="quantum-kpi-title">
            <Sparkles style={{ width: 14, height: 14, color: "var(--accent-cyan)" }} /> {isSignedOff ? "Agreed Contract Lump Sum" : "Contract Lump Sum"}
          </div>
          <div className="quantum-kpi-value" style={{ color: "#ffffff" }}>
            {sgd(currentLumpSum)}
          </div>
          <div className="quantum-kpi-sub" style={{ color: grandTotalWithGst != null ? "var(--accent-cyan)" : "var(--text-muted)", fontWeight: 700 }}>
            {grandTotalWithGst != null ? `+9% GST: ${sgd(grandTotalWithGst)}` : "Pending surveyor"}
          </div>
        </div>

        <div className="quantum-kpi-card" style={{ borderColor: "rgba(16, 185, 129, 0.3)", background: "rgba(16, 185, 129, 0.05)" }}>
          <div className="quantum-kpi-title">
            <ArrowDownRight style={{ width: 14, height: 14, color: "#10b981" }} /> Reduction vs Workshop Claim
          </div>
          <div className="quantum-kpi-value" style={{ color: "#10b981" }}>
            {claimReduction != null ? `-${sgd(claimReduction)}` : "—"}
          </div>
          <div className="quantum-kpi-sub" style={{ color: reductionPct != null ? "#10b981" : "var(--text-muted)", fontWeight: 700 }}>
            {reductionPct != null ? `${reductionPct}% below workshop claim` : "Needs priced items and a lump sum"}
          </div>
        </div>

        <div className="quantum-kpi-card">
          <div className="quantum-kpi-title">
            <Clock style={{ width: 14, height: 14, color: "#f59e0b" }} /> Repair Cycle Time SLA
          </div>
          <div className="quantum-kpi-value" style={{ color: "#f59e0b" }}>
            {repairTurnaroundDays != null ? `${repairTurnaroundDays} Working Days` : "—"}
          </div>
          <div className="quantum-kpi-sub">
            ATP: <span style={{ color: isSignedOff ? "#10b981" : "var(--text-muted)", fontWeight: 700 }}>{isSignedOff ? "GRANTED & SEALED" : "PENDING ENDORSEMENT"}</span>
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
              <Wrench style={{ width: 14, height: 14 }} /> Annex B: Labour & Refinishing ({annexBItems.length})
            </button>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "annexC" ? "active" : ""}`}
              onClick={() => setActiveSubTab("annexC")}
            >
              <Percent style={{ width: 14, height: 14 }} /> Annex C: Settlement & Waterfall
            </button>
            <button
              type="button"
              className={`annex-tab-btn ${activeSubTab === "bola" ? "active" : ""}`}
              onClick={() => setActiveSubTab("bola")}
            >
              <Scale style={{ width: 14, height: 14 }} /> Causation & BOLA Liability
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
                {!isSignedOff && (
                  <div style={{ fontSize: 11, color: "var(--badge-amber-text)", marginTop: 2 }}>
                    Changes made here are a working preview only and are not saved.
                  </div>
                )}
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
                  {annexAItems.length === 0 && (
                    <tr>
                      <td colSpan={9} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                        No parts recorded yet
                      </td>
                    </tr>
                  )}
                  {annexAItems.map((item, idx) => {
                    const isDisallowed = item.action === "Disallow" || item.discount_pct >= 0.99;
                    // Placeholder rows carry 0 until a surveyor prices them.
                    const isUnpriced = !item.workshop_est;
                    return (
                      <tr key={idx} style={{ opacity: isDisallowed ? 0.65 : 1 }}>
                        <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{item.item_no || `${idx + 1}.0`}</td>
                        <td style={{ fontWeight: 600 }}>{item.part_name}</td>
                        <td>
                          {item.condition ? (
                            <span className={`annex-action-badge ${item.condition === "Serviceable" ? "disallow" : item.condition.includes("Deformed") ? "replace" : "repair"}`}>
                              {item.condition}
                            </span>
                          ) : (
                            "—"
                          )}
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
                          {isUnpriced ? "—" : sgd(item.workshop_est * (item.qty || 1))}
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
                          {isUnpriced ? "—" : sgd(item.adjusted_cost)}
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
                      {hasPricedParts ? sgd(totalPartsClaimed) : "—"}
                    </td>
                    <td style={{ textAlign: "center", color: "#10b981", fontSize: 11 }}>
                      {hasPricedParts ? `-${sgd(totalPartsClaimed - totalPartsAdjusted)}` : "—"}
                    </td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>
                      {hasPricedParts ? sgd(totalPartsAdjusted) : "—"}
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
                  {annexBItems.length === 0 && (
                    <tr>
                      <td colSpan={5} style={{ textAlign: "center", color: "var(--text-muted)", padding: "18px 10px" }}>
                        No labour recorded yet
                      </td>
                    </tr>
                  )}
                  {annexBItems.map((lab, idx) => (
                    <tr key={idx}>
                      <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>{lab.item_no || `${idx + 1}.0`}</td>
                      <td style={{ fontWeight: 600 }}>{lab.description}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--text-secondary)" }}>
                        {lab.workshop_est ? sgd(lab.workshop_est) : "—"}
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)" }}>
                        {lab.workshop_est ? sgd(lab.adjusted_cost) : "—"}
                      </td>
                      <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                        {lab.justification || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2} style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      Total For Labour & Spray Painting (Annex B)
                    </td>
                    <td style={{ textAlign: "right", color: "var(--text-secondary)" }}>
                      {hasPricedLabour ? sgd(totalLabourClaimed) : "—"}
                    </td>
                    <td style={{ textAlign: "right", color: "var(--accent-cyan)", fontSize: 13 }}>
                      {hasPricedLabour ? sgd(totalLabourAdjusted) : "—"}
                    </td>
                    <td style={{ color: "#10b981", fontSize: 11 }}>
                      Variance: {hasPricedLabour ? `-${sgd(totalLabourClaimed - totalLabourAdjusted)}` : "—"}
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
                    <span style={{ color: "var(--text-muted)" }}>Claimed: {hasPricedParts ? sgd(totalPartsClaimed) : "—"}</span>
                    <span className="waterfall-val">{hasPricedParts ? sgd(totalPartsAdjusted) : "—"}</span>
                  </div>
                </div>

                <div className="waterfall-row">
                  <span className="waterfall-label">2. Annex B: Labour & Spray Painting Total</span>
                  <div style={{ display: "flex", gap: 20 }}>
                    <span style={{ color: "var(--text-muted)" }}>Claimed: {hasPricedLabour ? sgd(totalLabourClaimed) : "—"}</span>
                    <span className="waterfall-val">{hasPricedLabour ? sgd(totalLabourAdjusted) : "—"}</span>
                  </div>
                </div>

                <div className="waterfall-row subtotal">
                  <span>3. Total Repair Costs (Part-by-Part Adjusted)</span>
                  <div style={{ display: "flex", gap: 20 }}>
                    <span style={{ color: "var(--text-muted)", textDecoration: hasPricedItems ? "line-through" : "none" }}>{hasPricedItems ? sgd(grossClaimedTotal) : "—"}</span>
                    <span style={{ color: "var(--accent-cyan)", fontFamily: "var(--font-mono)" }}>{hasPricedItems ? sgd(grossAdjustedTotal) : "—"}</span>
                  </div>
                </div>

                <div className="waterfall-row" style={{ background: "rgba(56, 189, 248, 0.05)", padding: "10px 14px", borderRadius: 6 }}>
                  <div>
                    <div style={{ fontWeight: 700, color: "var(--text-primary)" }}>4. Recommended Contract Lump Sum Settlement</div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      {currentLumpSum != null ? "Negotiated commercial concession with repairer discretion" : "Pending surveyor"}
                    </div>
                  </div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, fontWeight: 800, color: "var(--accent-cyan)" }}>
                    {sgd(currentLumpSum)}
                  </div>
                </div>

                <div className="waterfall-row">
                  <span className="waterfall-label">5. Prevailing Singapore GST (9%)</span>
                  <span className="waterfall-val">{sgd(gstAmount)}</span>
                </div>

                <div className="waterfall-row grand">
                  <div>
                    <div>Grand Total Payable (Including 9% GST)</div>
                    <div style={{ fontSize: 11, fontWeight: 400, opacity: 0.8 }}>Full & final discharge on contract lump sum basis</div>
                  </div>
                  <div style={{ fontSize: 20, fontFamily: "var(--font-mono)" }}>
                    {sgd(grandTotalWithGst)}
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 8, fontSize: 11 }}>
                  <span style={{ color: "#10b981", fontWeight: 700 }}>Reduction vs Workshop Claim:</span>
                  <span style={{ fontFamily: "var(--font-mono)", color: "#10b981", fontWeight: 800 }}>
                    {claimReduction != null ? `${sgd(claimReduction)} (${reductionPct}%)` : "—"}
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
                    {/* Terms state an agreed figure only once one exists. */}
                    <p style={{ margin: 0 }}>
                      {currentLumpSum != null ? (
                        <>
                          <strong>Repairer Discretion:</strong> The repairer has agreed to undertake the repairs on a contract lump sum of{" "}
                          <strong style={{ color: "var(--text-primary)" }}>{sgd(currentLumpSum)}</strong>, according to acceptable quality and standard (Repairer discretion to repair parts, replace with reconditioned/used parts, or replace with OEM/genuine parts).
                        </>
                      ) : (
                        <>
                          <strong>Repairer Discretion:</strong> No contract lump sum agreed yet. <strong style={{ color: "var(--text-muted)" }}>Pending surveyor.</strong>
                        </>
                      )}
                    </p>
                    <p style={{ margin: 0 }}>
                      {repairTurnaroundDays != null ? (
                        <>
                          <strong>Repair Turnaround Period:</strong> The entire repair of the damaged vehicle should be completed within a reasonable period of{" "}
                          <strong style={{ color: "#f59e0b" }}>{repairTurnaroundDays} working days</strong> to mitigate consequential loss of use claims.
                        </>
                      ) : (
                        <>
                          <strong>Repair Turnaround Period:</strong> Not set yet. <strong style={{ color: "var(--text-muted)" }}>Pending surveyor.</strong>
                        </>
                      )}
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
                    <div style={{ fontWeight: 700, marginTop: 2, color: v?.point_of_impact ? undefined : "var(--text-muted)" }}>{v?.point_of_impact || "Pending surveyor"}</div>
                  </div>
                  {/* The three findings below aren't recorded fields. They were
                      fixed text on every case (a 380-860 mm contact zone,
                      "100% CONSISTENT", serviceable tailgate hinges). */}
                  <div>
                    <div className="detail-field-label">Impact Height & Direction of Force</div>
                    <div style={{ marginTop: 2, color: "var(--text-muted)" }}>
                      Pending surveyor
                    </div>
                  </div>
                  <div>
                    <div className="detail-field-label">Narrative Consistency Finding</div>
                    <div style={{ marginTop: 2, color: "var(--text-muted)" }}>
                      Pending surveyor
                    </div>
                  </div>
                  <div>
                    <div className="detail-field-label">Pre-Existing / Wear Exclusions</div>
                    <div style={{ marginTop: 2, color: "var(--text-muted)" }}>
                      Pending surveyor
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
                  {/* Everything in this card is d.bola_assessment as recorded
                      (it was a fixed Scenario #14, 0% / 100%, on every case). */}
                  <span className="badge-plate-glow" style={{ fontSize: 10, padding: "2px 8px" }}>
                    {bolaScenario != null ? `RULE #${bolaScenario}` : "PENDING"}
                  </span>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: bolaScenario != null ? "var(--text-primary)" : "var(--text-muted)" }}>
                      {bolaScenario != null
                        ? `BOLA Scenario #${bolaScenario}${bola?.scenario_name ? `: ${bola.scenario_name}` : ""}`
                        : "Pending surveyor assessment"}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      {bola?.apportionment_rationale || "No apportionment rationale recorded."}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                      Status: {bola?.dispute_status || "Pending surveyor assessment"}
                    </div>
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div style={{ background: "rgba(16, 185, 129, 0.1)", padding: "10px 12px", borderRadius: 8, border: "1px solid rgba(16, 185, 129, 0.3)" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "#10b981", textTransform: "uppercase" }}>Insured Liability</div>
                      <div style={{ fontSize: 20, fontWeight: 800, fontFamily: "var(--font-mono)", color: "#10b981" }}>{insuredPct != null ? `${insuredPct}%` : "—"}</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {insuredPct == null ? "Pending surveyor" : insuredPct === 0 ? "Zero contributory fault" : insuredPct === 100 ? "Sole fault" : "Contributory fault"}
                      </div>
                    </div>

                    <div style={{ background: "rgba(239, 68, 68, 0.1)", padding: "10px 12px", borderRadius: 8, border: "1px solid rgba(239, 68, 68, 0.3)" }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "#ef4444", textTransform: "uppercase" }}>Third Party Liability</div>
                      <div style={{ fontSize: 20, fontWeight: 800, fontFamily: "var(--font-mono)", color: "#ef4444" }}>{tpPct != null ? `${tpPct}%` : "—"}</div>
                      <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                        {tpPct == null ? "Pending surveyor" : tpPct === 100 ? "Sole tortfeasor fault" : tpPct === 0 ? "No fault" : "Shared fault"}
                      </div>
                    </div>
                  </div>

                  <div>
                    <div className="detail-field-label">Subrogation Recovery Prospect</div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: bola?.subrogation_prospect ? "var(--accent-cyan)" : "var(--text-muted)", marginTop: 2 }}>
                      {bola?.subrogation_prospect || "Pending surveyor assessment"}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                      Target Insurer: {ins?.insurer_name || "—"} • Counterpart Ref: {ins?.claim_number || "—"}
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
              <h3 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>Loss Adjuster Statutory Endorsement & Certification</h3>
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
              <ShieldCheck style={{ width: 16, height: 16 }} /> CERTIFIED & LOCKED DOSSIER
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
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{firmName || "Firm not set"}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)" }}>Firm Registry</div>
            <div style={{ fontSize: 12, fontWeight: 700 }}>60 Hillside Drive, S549009</div>
            <div style={{ fontSize: 11, color: "var(--text-muted)" }}>Tel: 6285 6178 / Fax: 6287 4788</div>
          </div>
        </div>

        {/* What sign-off records into Annex C / the BOLA assessment. Empty
            unless already recorded on the case; sent only if filled in. */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-muted)" }}>
            Settlement & Liability Findings
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
              Agreed lump sum (S$, before GST)
              <input
                type="number"
                min={0}
                step="0.01"
                value={contractLumpSum ?? ""}
                placeholder="Not recorded"
                disabled={isSignedOff}
                onChange={(e) => setContractLumpSum(e.target.value === "" ? null : Number(e.target.value))}
                style={fieldInputStyle}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
              Repair turnaround (working days)
              <input
                type="number"
                min={0}
                step="1"
                value={repairTurnaroundDays ?? ""}
                placeholder="Not recorded"
                disabled={isSignedOff}
                onChange={(e) => setRepairTurnaroundDays(e.target.value === "" ? null : Number(e.target.value))}
                style={fieldInputStyle}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
              Liability opinion
              <input
                type="text"
                value={liabilityOpinion}
                placeholder="Pending surveyor assessment"
                disabled={isSignedOff}
                onChange={(e) => setLiabilityOpinion(e.target.value)}
                style={fieldInputStyle}
              />
            </label>
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
                {/* A signature only once signed off -- this used to render the
                    surveyor's name as a cursive signature on unsigned cases. */}
                {isSignedOff ? (
                  <>
                    <div style={{ fontFamily: "Georgia, serif", fontStyle: "italic", fontSize: 24, color: "var(--accent-cyan)", letterSpacing: "0.05em" }}>
                      {signOff?.surveyor_name || surveyorName}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 4 }}>
                      Automotive Engineer Assessor • Reg: {signOff?.license_number || licenseNumber}
                    </div>
                    {signOff?.signature_date && (
                      <div suppressHydrationWarning style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-muted)", marginTop: 4 }}>
                        Endorsed on: {new Date(signOff.signature_date).toLocaleString("en-SG")}
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                    Not signed. The digital seal is applied on sign-off.
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Official Company Seal Preview */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", color: "var(--text-muted)", marginBottom: 6 }}>
              Official Assessor Chop & Seal
            </div>
            <div className="surveyor-chop-preview">
              {/* The seal only once signed off, naming who signed and the real
                  hash. It used to show on every case with a fixed name and a
                  placeholder hash. */}
              {isSignedOff ? (
                <>
                  <div style={{ fontSize: 12, fontWeight: 800, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-primary)" }}>
                    {signOff?.firm_name || "Carlink Consultancy"}
                  </div>
                  <div style={{ fontSize: 9, color: "var(--text-muted)" }}>
                    60 Hillside Drive, Singapore 549009
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--accent-primary)", marginTop: 4, textTransform: "uppercase" }}>
                    {signOff?.surveyor_name || surveyorName} • AUTOMOTIVE ENGINEER ASSESSOR
                  </div>
                  <div style={{ fontSize: 9, fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                    {signOff?.qualifications || qualifications}
                  </div>
                  {sealHash && (
                    <div style={{ fontSize: 8, fontFamily: "var(--font-mono)", color: "var(--accent-cyan)", marginTop: 4 }}>
                      SHA-256 SEAL: {sealHash}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Seal not applied. Case not signed off.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Action Button Strip */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10, borderTop: "1px solid var(--border-color)", flexWrap: "wrap", gap: 10 }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {isSignedOff
              ? "This case is locked. To modify line items, reopen the report from the edit tab."
              : "Signing off records the lump sum, repair days and liability opinion entered above, embeds the digital seal into the PDF, and locks the case. Annex A edits on this tab are not saved."}
          </div>

          {signOffError && (
            <div style={{ color: "var(--danger, #ef4444)", fontSize: 12 }}>
              {signOffError}
            </div>
          )}

          {profileIncomplete && (
            <div style={{ color: "var(--badge-amber-text)", fontSize: 12 }}>
              Your profile has no licence number, so the report will print without one.{" "}
              <a href="/account" className="setup-help-link">Add it on My account</a>
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
            <a href={pdfDownloadUrl(report.id)} className="btn-secondary-modern">
              <Printer style={{ width: 14, height: 14 }} /> Download Certified PDF Dossier
            </a>
          )}
        </div>
      </div>
    </div>
  );
}
