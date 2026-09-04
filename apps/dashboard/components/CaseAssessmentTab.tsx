import type { ReportDetail, DamageSummaryItem } from "@/lib/api";
import { CaseAnalysisPanel } from "./CaseAnalysisPanel";

function Field({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div>
      <div className="detail-field-label">{label}</div>
      <div
        style={{
          fontWeight: mono ? 700 : 600,
          marginTop: 2,
          fontSize: 13,
          wordBreak: "break-word",
          fontFamily: mono ? "var(--font-mono)" : undefined,
          color: mono ? "var(--accent-cyan)" : undefined,
        }}
      >
        {value || "—"}
      </div>
    </div>
  );
}

/** The assessor's conclusions and the claim paperwork -- what happens to
 * the vehicle and the claim, as opposed to the damage inspection itself.
 * Cards render only when they hold real data, so a report filed without
 * recommendations or a claim doesn't show empty scaffolding. */
export function CaseAssessmentTab({
  report,
  damageEntries,
}: {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
}) {
  const d = report.data;
  const pol = d.police_report;
  const ins = d.insurance_details;
  const rec = d.recommendations;
  const signOff = d.sign_off;
  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";

  const recRows = [
    { label: "🔧 Repair", value: rec?.repair_recommendation },
    { label: "🔄 Replacement", value: rec?.replacement_recommendation },
    { label: "🔍 Inspection", value: rec?.inspection_recommendation },
    { label: "➡️ Follow-Up", value: rec?.follow_up_action },
    { label: "🛡 Preventive", value: rec?.preventive_action },
  ].filter((r) => Boolean(r.value));

  const hasRecommendations = recRows.length > 0 || rec?.disassembly_required;
  const hasCost = Boolean(ins?.estimated_repair_cost || ins?.final_approved_cost);
  const hasClaim = Boolean(
    ins?.claim_number || ins?.claim_type || ins?.claim_status || ins?.adjuster_assigned || ins?.workshop_assigned || ins?.insurer_name || ins?.policy_number
  );
  const hasPolice = Boolean(
    pol?.reported_to_police || pol?.police_station || pol?.report_number || pol?.officer_name || pol?.date_reported || pol?.reference_number
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Leads the tab: "what does this job involve and is it ready to
          finalize" is the question this whole tab exists to answer. */}
      <CaseAnalysisPanel report={report} damageEntries={damageEntries} />

      {hasRecommendations && (
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>🔧</span> Assessor Recommendations
              </div>
              <div className="card-subtitle">Actions advised by the surveyor after inspection</div>
            </div>
            {rec?.disassembly_required && (
              <span className="chip-severity moderate" style={{ fontSize: 10 }}>
                ⚠️ Disassembly Required
              </span>
            )}
          </div>
          {recRows.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {recRows.map((r, i) => (
                <div key={i} className="recommendation-row">
                  <div className="recommendation-label">{r.label}</div>
                  <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>{r.value}</div>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
              No written recommendations recorded.
            </p>
          )}
        </div>
      )}

      <div className="case-file-split">
        {hasCost && (
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>💰</span> Repair Cost
              </div>
            </div>
            <div className="cost-matrix-glow">
              <div className="cost-row total">
                <span>Estimated</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700 }}>{ins?.estimated_repair_cost || "—"}</span>
              </div>
              <div className="cost-row">
                <span style={{ color: "var(--text-muted)" }}>Final Approved</span>
                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: ins?.final_approved_cost ? undefined : "var(--text-muted)" }}>
                  {ins?.final_approved_cost || "— pending"}
                </span>
              </div>
            </div>
          </div>
        )}

        {hasClaim && (
          <div className="card-glass">
            <div className="card-header">
              <div className="card-title">
                <span>📋</span> Claim Details
              </div>
              {ins?.claim_status && (
                <span className="chip-severity minor" style={{ fontSize: 10 }}>
                  {ins.claim_status}
                </span>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Claim Number" value={ins?.claim_number} mono />
              <Field label="Claim Type" value={ins?.claim_type} />
              <Field label="Insurer" value={ins?.insurer_name} />
              <Field label="Policy Number" value={ins?.policy_number} />
            </div>
            {(ins?.adjuster_assigned || ins?.workshop_assigned) && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border-color)" }}>
                <Field label="Adjuster Assigned" value={ins?.adjuster_assigned} />
                <Field label="Workshop Assigned" value={ins?.workshop_assigned} />
              </div>
            )}
          </div>
        )}
      </div>

      <div className="case-file-split">
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🏛</span> Police Report
            </div>
          </div>
          {!hasPolice ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>Not reported to police.</p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Police Station" value={pol?.police_station} />
              <Field label="Report Number" value={pol?.report_number} mono />
              <Field label="Officer" value={pol?.officer_name} />
              <Field label="Date Reported" value={pol?.date_reported} />
              <Field label="Reference Number" value={pol?.reference_number || d.authority_reference} mono />
            </div>
          )}
        </div>

        {/* Sign-Off Status -- surfaces sign_off.prepared_by/reviewed_by/
            approved_by/signature_date, which existed in the schema and was
            being saved (see signOffReportAction) but was never actually
            shown anywhere in the studio before this. */}
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>✍️</span> Sign-Off Status
            </div>
            <span className={`chip-severity ${isSignedOff ? "minor" : "moderate"}`} style={{ fontSize: 10 }}>
              {isSignedOff ? "✓ Locked" : "Pending"}
            </span>
          </div>
          {isSignedOff ? (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Signed On" value={signOff?.signature_date ? new Date(signOff.signature_date).toLocaleString() : null} />
              <Field label="Prepared By" value={signOff?.prepared_by} />
              <Field label="Reviewed By" value={signOff?.reviewed_by} />
              <Field label="Approved By" value={signOff?.approved_by} />
            </div>
          ) : (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
              Not yet signed off. Use the &quot;Finalize &amp; Sign Off&quot; button above to verify and lock this report.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
