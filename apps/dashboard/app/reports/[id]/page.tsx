import Link from "next/link";
import { notFound } from "next/navigation";
import { getReport, fileUrl, pdfDownloadUrl } from "@/lib/api";
import { ChannelBadge } from "@/components/ChannelBadge";
import { DeleteButton } from "@/components/DeleteButton";
import { SignOffButton } from "@/components/SignOffButton";
import { ImageLightbox } from "@/components/ImageLightbox";
import { PdfPreviewModal } from "@/components/PdfPreviewModal";

export default async function ReportDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ preview?: string }>;
}) {
  const { id } = await params;
  const { preview } = await searchParams;
  const report = await getReport(id);
  if (!report) notFound();

  const d = report.data;
  const v = d.vehicle_info;
  const pol = d.police_report;
  const ins = d.insurance_details;
  const rec = d.recommendations;
  const sign = d.sign_off;
  const ai = d.ai_analysis;

  return (
    <div style={{ paddingBottom: 40 }}>
      {/* Header Banner */}
      <div className="page-header" style={{ alignItems: "flex-start" }}>
        <div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 4 }}>
            <span className="status-pill" style={{ background: "#2563eb", color: "#fff", padding: "4px 10px", borderRadius: 4 }}>
              {d.report_id || `CIR-2026-${report.id.slice(0, 4).toUpperCase()}`}
            </span>
            <span className="status-pill">{sign?.status || report.status}</span>
            <ChannelBadge channel={report.channel} />
          </div>
          <h1 style={{ margin: 0, fontSize: 24 }}>
            {d.location || (d.accident_type ? "Car Incident Report" : "Security Incident Report")}
          </h1>
          <p style={{ margin: "4px 0 0", color: "var(--text-muted)", fontSize: 13 }}>
            Filed on {new Date(report.created_at).toLocaleString()} &middot; Prepared by {d.reporter_name || "—"}
          </p>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Link href={`/reports/${report.id}/edit`} className="button-primary">
            ✏️ Edit
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

      {/* Main Grid: Overview & Vehicle Details */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 12 }}>Vehicle & Incident Overview</h2>
        <div className="detail-grid">
          <div>
            <div className="detail-field-label">Vehicle Plate</div>
            <div className="detail-field-value" style={{ marginTop: 4 }}>
              {v?.plate_number || d.vehicle_details ? (
                <span className="plate-badge">{v?.plate_number || d.vehicle_details}</span>
              ) : (
                "—"
              )}
            </div>
          </div>
          <div>
            <div className="detail-field-label">Make &amp; Model</div>
            <div className="detail-field-value">
              {v?.make || v?.model ? `${v?.make || ""} ${v?.model || ""}`.trim() : "—"}
            </div>
          </div>
          <div>
            <div className="detail-field-label">Accident Type</div>
            <div className="detail-field-value">
              <strong>{d.accident_type || "—"}</strong>
            </div>
          </div>
          <div>
            <div className="detail-field-label">Overall Severity</div>
            <div className="detail-field-value">
              {d.severity_level ? (
                <span
                  style={{
                    display: "inline-block",
                    padding: "2px 10px",
                    borderRadius: 12,
                    fontSize: 12,
                    fontWeight: "bold",
                    background: d.severity_level === "Severe" ? "#fee2e2" : d.severity_level === "Moderate" ? "#fef3c7" : "#dcfce7",
                    color: d.severity_level === "Severe" ? "#991b1b" : d.severity_level === "Moderate" ? "#92400e" : "#166534",
                  }}
                >
                  {d.severity_level}
                </span>
              ) : (
                "—"
              )}
            </div>
          </div>
          <div>
            <div className="detail-field-label">Reporter Name &amp; Role</div>
            <div className="detail-field-value">
              {d.reporter_name || "—"}{d.reporter_role ? ` (${d.reporter_role})` : ""}
            </div>
          </div>
          <div>
            <div className="detail-field-label">Incident Date / Time</div>
            <div className="detail-field-value">{d.incident_datetime || "—"}</div>
          </div>
          <div>
            <div className="detail-field-label">Weather & Road</div>
            <div className="detail-field-value">
              {d.weather_condition || d.road_condition
                ? `${d.weather_condition || "—"} / ${d.road_condition || "—"}`
                : "—"}
            </div>
          </div>
          <div>
            <div className="detail-field-label">Category</div>
            <div className="detail-field-value">
              {d.category?.length
                ? d.category.map((c) => (
                    <span key={c} className="tag">
                      {c}
                    </span>
                  ))
                : "—"}
            </div>
          </div>
        </div>
      </div>

      {/* Structured Damage Summary Table */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 style={{ fontSize: 16, margin: 0 }}>Structured Damage Summary</h2>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Photo-linked Parts Checklist</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          {(() => {
            // Only ever built from real data -- damage_summary items when they
            // exist, otherwise the plain damaged_parts list with everything
            // else honestly left unknown. Never invented from keyword-sniffing
            // the description (that's how "Alex Wong"-style fabrication crept
            // in before: a plausible-looking guess standing in for real data).
            const damageItems = (d.damage_summary && d.damage_summary.length > 0)
              ? d.damage_summary
              : (d.damaged_parts && d.damaged_parts.length > 0)
              ? d.damaged_parts.map((p) => ({
                  part: p,
                  damage_type: null as string | null,
                  severity: d.severity_level || null,
                  photo_reference: null as string | null,
                  ai_confidence: null as string | null,
                  human_verified: false,
                }))
              : [];

            if (damageItems.length === 0) {
              return (
                <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
                  No structured damage recorded.
                </p>
              );
            }

            return (
              <table className="reports-table" style={{ width: "100%", textAlign: "left" }}>
                <thead>
                  <tr>
                    <th>Damaged Part</th>
                    <th>Damage Type</th>
                    <th>Severity</th>
                    <th>Photo Ref</th>
                    <th>AI Confidence</th>
                    <th>Verified</th>
                  </tr>
                </thead>
                <tbody>
                  {damageItems.map((item, idx) => (
                    <tr key={idx}>
                      <td>
                        <strong>{item.part}</strong>
                      </td>
                      <td>{item.damage_type || "—"}</td>
                      <td>
                        {item.severity ? (
                          <span
                            style={{
                              padding: "2px 8px",
                              borderRadius: 10,
                              fontSize: 11,
                              fontWeight: 600,
                              background: item.severity === "Severe" ? "var(--badge-red-bg)" : "var(--badge-amber-bg)",
                              color: item.severity === "Severe" ? "var(--badge-red-text)" : "var(--badge-amber-text)",
                            }}
                          >
                            {item.severity}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        {item.photo_reference && report.photo_urls.length > 0 ? (
                          <a href={`#photo-${item.photo_reference}`} style={{ fontWeight: 600 }}>
                            {item.photo_reference}
                          </a>
                        ) : (
                          item.photo_reference || "—"
                        )}
                      </td>
                      <td>{item.ai_confidence || "—"}</td>
                      <td>
                        {item.human_verified ? (
                          <span style={{ color: "#16a34a", fontWeight: "bold" }}>✓ Verified</span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>Pending</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })()}
        </div>
      </div>

      {/* Description */}
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 8 }}>Incident Description & Narrative</h2>
        <p style={{ fontSize: 14, lineHeight: 1.6, margin: 0, whiteSpace: "pre-wrap" }}>{d.description}</p>
      </div>

      {/* Police Report & Insurance Claim Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <div className="card">
          <h2 style={{ fontSize: 16, marginBottom: 10 }}>Authority & Police Report</h2>
          <div className="detail-grid" style={{ gridTemplateColumns: "1fr" }}>
            <div>
              <div className="detail-field-label">Reported to Police</div>
              <div className="detail-field-value">{pol?.reported_to_police || d.reported_to_authorities ? "Yes" : "No"}</div>
            </div>
            {pol?.police_station && (
              <div>
                <div className="detail-field-label">Police Station</div>
                <div className="detail-field-value">{pol.police_station}</div>
              </div>
            )}
            <div>
              <div className="detail-field-label">Report / Reference Number</div>
              <div className="detail-field-value">{pol?.report_number || d.authority_reference || "—"}</div>
            </div>
          </div>
        </div>

        {(ins?.insurer_name || ins?.claim_number || ins?.claim_type || ins?.estimated_repair_cost) && (
          <div className="card">
            <h2 style={{ fontSize: 16, marginBottom: 10 }}>Insurance & Claim Details</h2>
            <div className="detail-grid" style={{ gridTemplateColumns: "1fr" }}>
              <div>
                <div className="detail-field-label">Insurer Name</div>
                <div className="detail-field-value">{ins?.insurer_name || "—"}</div>
              </div>
              <div>
                <div className="detail-field-label">Claim Type / Status</div>
                <div className="detail-field-value">
                  {ins?.claim_type || "—"}
                  {ins?.claim_status && (
                    <>
                      {" "}&middot; <span className="status-pill">{ins.claim_status}</span>
                    </>
                  )}
                </div>
              </div>
              <div>
                <div className="detail-field-label">Estimated Repair Cost</div>
                <div className="detail-field-value">{ins?.estimated_repair_cost || "—"}</div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* AI Vision Analysis Box -- only shown when there's real AI output to show */}
      {(ai?.confidence_score || ai?.summary || ai?.suggested_category || (d.damaged_parts && d.damaged_parts.length > 0)) && (
        <div className="card" style={{ marginBottom: 16, background: "#f0f9ff", borderColor: "#bae6fd" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#0369a1", marginBottom: 8 }}>
            <span style={{ fontSize: 18 }}>🤖</span>
            <h2 style={{ fontSize: 15, margin: 0, color: "#0369a1" }}>AI Computer Vision Analysis</h2>
          </div>
          <p style={{ fontSize: 13, color: "#0c4a6e", margin: "0 0 8px" }}>
            {ai?.confidence_score && (
              <>AI Confidence Score: <strong>{ai.confidence_score}</strong> &middot; </>
            )}
            Suggested Category: <strong>{ai?.suggested_category || d.accident_type || "—"}</strong>
          </p>
          {ai?.summary && <p style={{ fontSize: 13, color: "#0369a1", margin: 0 }}><em>"{ai.summary}"</em></p>}
        </div>
      )}

      {/* Timeline & Recommendations -- both only show real, reporter-confirmed
          data. A fabricated timeline previously claimed events ("Telegram
          Bot: AI extracted report draft") that hadn't happened -- including
          on reports filed manually, with no Telegram bot involved at all. */}
      {(d.timeline?.length || rec?.repair_recommendation || rec?.inspection_recommendation) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
          {d.timeline && d.timeline.length > 0 && (
            <div className="card">
              <h2 style={{ fontSize: 16, marginBottom: 10 }}>Timeline of Events</h2>
              <ul style={{ paddingLeft: 20, margin: 0, fontSize: 13, lineHeight: 1.8 }}>
                {d.timeline.map((ev, i) => (
                  <li key={i}>
                    <strong>{ev.time}:</strong> {ev.event}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(rec?.repair_recommendation || rec?.inspection_recommendation) && (
            <div className="card">
              <h2 style={{ fontSize: 16, marginBottom: 10 }}>Recommendations & Next Steps</h2>
              {rec?.repair_recommendation && (
                <p style={{ fontSize: 13, lineHeight: 1.6, margin: 0 }}>
                  <strong>Repair Recommendation:</strong> {rec.repair_recommendation}
                </p>
              )}
              {rec?.inspection_recommendation && (
                <p style={{ fontSize: 13, lineHeight: 1.6, margin: "6px 0 0" }}>
                  <strong>Inspection:</strong> {rec.inspection_recommendation}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Photos Section */}
      {report.photo_urls.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, marginBottom: 12 }}>Photo Evidence Gallery ({report.photo_urls.length})</h2>
          <ImageLightbox photoUrls={report.photo_urls} />
        </div>
      )}

      {/* Audit Trail Log */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginBottom: 10 }}>Audit Trail &amp; System Log</h2>
        <ul style={{ paddingLeft: 20, margin: 0, fontSize: 13, lineHeight: 1.8, color: "var(--muted)" }}>
          <li>
            <strong>{new Date(report.created_at).toLocaleString()}</strong> &middot; Incident report created via <code>{report.channel}</code> channel.
          </li>
          {/* Telegram/WhatsApp always run photos+text through the AI drafter.
              A manual entry only did if the reporter used "Analyze with AI" --
              the only way a manual report ever gets photos attached -- so
              this no longer claims AI involvement that didn't happen. */}
          {(report.channel !== "manual" || report.photo_urls.length > 0) && (
            <li>
              <strong>{new Date(report.created_at).toLocaleString()}</strong> &middot; Multimodal AI Vision engine drafted structured incident data.
            </li>
          )}
          {sign?.status === "Signed Off" && (
            <li>
              <strong>{new Date().toLocaleString()}</strong> &middot; Report reviewed &amp; signed off by <strong>{sign.reviewed_by || "Surveyor"}</strong>. Official PDF generated and locked.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
