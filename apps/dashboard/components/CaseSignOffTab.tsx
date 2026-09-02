import type { ReportDetail } from "@/lib/api";

export function CaseSignOffTab({ report }: { report: ReportDetail }) {
  const d = report.data;
  const pol = d.police_report;
  const ins = d.insurance_details;
  const signOff = d.sign_off;
  const isSignedOff = report.status === "Signed Off" || signOff?.status === "Signed Off";

  const hasPoliceOrInsurance = Boolean(
    pol?.police_station || pol?.report_number || ins?.insurer_name || ins?.policy_number || ins?.claim_type
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Sign-Off Status -- surfaces sign_off.prepared_by/reviewed_by/
          approved_by/signature_date, which existed in the schema and was
          being saved (see signOffReportAction) but was never actually
          shown anywhere in the studio before this. */}
      <div className="card-glass">
        <div className="card-header">
          <div className="card-title">
            <span>✍️</span> Sign-Off Status
          </div>
        </div>
        {isSignedOff ? (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
            <div>
              <div className="detail-field-label">Status</div>
              <div style={{ fontWeight: 600, marginTop: 2 }}>
                <span className="chip-severity minor">✓ Signed Off &amp; Locked</span>
              </div>
            </div>
            {signOff?.signature_date && (
              <div>
                <div className="detail-field-label">Signed On</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{new Date(signOff.signature_date).toLocaleString()}</div>
              </div>
            )}
            {signOff?.prepared_by && (
              <div>
                <div className="detail-field-label">Prepared By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.prepared_by}</div>
              </div>
            )}
            {signOff?.reviewed_by && (
              <div>
                <div className="detail-field-label">Reviewed By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.reviewed_by}</div>
              </div>
            )}
            {signOff?.approved_by && (
              <div>
                <div className="detail-field-label">Approved By</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{signOff.approved_by}</div>
              </div>
            )}
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
            Not yet signed off. Use the &quot;Finalize &amp; Sign Off&quot; button above to lock this report.
          </p>
        )}
      </div>

      {/* Police & Insurance Details */}
      {hasPoliceOrInsurance && (
        <div className="card-glass">
          <div className="card-header">
            <div className="card-title">
              <span>🏛️</span> Authority &amp; Insurance Policy Details
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, fontSize: 13 }}>
            {pol?.police_station && (
              <div>
                <div className="detail-field-label">Police Station</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{pol.police_station}</div>
              </div>
            )}
            {pol?.report_number && (
              <div>
                <div className="detail-field-label">Report Reference Number</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2 }}>{pol.report_number}</div>
              </div>
            )}
            {ins?.insurer_name && (
              <div>
                <div className="detail-field-label">Insurance Provider</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{ins.insurer_name}</div>
              </div>
            )}
            {(ins?.policy_number || ins?.claim_type) && (
              <div>
                <div className="detail-field-label">Policy &amp; Claim Type</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>
                  {ins?.policy_number} {ins?.policy_number && ins?.claim_type && <>&bull;</>} {ins?.claim_type}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
