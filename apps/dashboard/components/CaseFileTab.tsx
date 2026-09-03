import type { ReportDetail } from "@/lib/api";

/** A single label/value pair. Renders an em-dash for a missing value
 * rather than a placeholder or an invented one -- an honest gap, matching
 * how the rest of this app treats unknown fields. */
function Field({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <div className="detail-field-label">{label}</div>
      <div style={{ fontWeight: 600, marginTop: 2, fontSize: 13, wordBreak: "break-word" }}>{value || "—"}</div>
    </div>
  );
}

/** Cards only render when they actually have something to show (same
 * pattern the authority/insurance card already used) -- a thin report
 * shouldn't render a wall of empty labelled boxes. */
function Card({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="card-glass">
      <div className="card-header">
        <div className="card-title">
          <span>{icon}</span> {title}
        </div>
      </div>
      {children}
    </div>
  );
}

export function CaseFileTab({ report }: { report: ReportDetail }) {
  const d = report.data;
  const v = d.vehicle_info;
  const timeline = d.timeline || [];
  const people = d.people_involved || [];
  const witnesses = d.witnesses || [];

  const hasReporter = Boolean(d.reporter_name || d.reporter_role || d.reporter_department || d.reporter_contact || d.reporter_email);
  const hasVehicle = Boolean(v?.plate_number || v?.make || v?.model || v?.year || v?.color || v?.vin || v?.engine_number || v?.driver_name);
  const hasNotes = Boolean(d.immediate_actions || d.additional_comments || d.preventive_measures);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="case-file-split">
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {hasReporter && (
            <Card icon="👤" title="Reporter / Assessor">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                <Field label="Name" value={d.reporter_name} />
                <Field label="Role" value={d.reporter_role} />
                <Field label="Organisation" value={d.reporter_department || d.company_name} />
                <Field label="Contact" value={d.reporter_contact || d.reporter_email} />
              </div>
            </Card>
          )}

          <Card icon="📅" title="Incident Details">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Date / Time" value={d.incident_datetime} />
              <Field label="Accident Type" value={d.accident_type} />
              <Field label="Location" value={d.location} />
              <Field label="Severity" value={d.severity_level} />
              <Field label="Category" value={(d.category || []).join(", ")} />
              <Field label="Filed Via" value={report.channel} />
            </div>
            {(d.weather_condition || d.road_condition || d.traffic_condition) && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
                {[d.weather_condition, d.road_condition, d.traffic_condition]
                  .filter(Boolean)
                  .map((c, i) => (
                    <span key={i} className="chip-severity minor" style={{ fontSize: 10 }}>
                      {c}
                    </span>
                  ))}
              </div>
            )}
          </Card>
        </div>

        {hasVehicle && (
          <Card icon="🚘" title="Vehicle Identity">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <Field label="Plate Number" value={v?.plate_number} />
              <Field label="Make" value={v?.make} />
              <Field label="Model" value={v?.model} />
              <Field label="Year" value={v?.year} />
              <Field label="Colour" value={v?.color} />
              <Field label="Ownership" value={v?.ownership_type} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border-color)" }}>
              <div>
                <div className="detail-field-label">VIN / Chassis</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2, fontSize: 13 }}>
                  {v?.vin || "—"}
                </div>
              </div>
              <div>
                <div className="detail-field-label">Engine Number</div>
                <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-cyan)", marginTop: 2, fontSize: 13 }}>
                  {v?.engine_number || "—"}
                </div>
              </div>
            </div>
            {(v?.driver_name || v?.driver_contact) && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border-color)" }}>
                <Field label="Driver" value={v?.driver_name} />
                <Field label="Driver Contact" value={v?.driver_contact} />
              </div>
            )}
          </Card>
        )}
      </div>

      {timeline.length > 0 && (
        <Card icon="🕒" title="Incident Timeline">
          <div className="case-timeline">
            {timeline.map((event, i) => (
              <div key={i} className="case-timeline-item">
                <div className="case-timeline-dot" />
                <div className="case-timeline-time">{event.time}</div>
                <div className="case-timeline-event">{event.event}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="case-file-split">
        <Card icon="👥" title="People & Witnesses">
          <div className="detail-field-label" style={{ marginBottom: 8 }}>People Involved</div>
          {people.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "0 0 14px" }}>None recorded.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14 }}>
              {people.map((p, i) => (
                <div key={i} className="person-row">
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{p.name}</div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
                    {[p.role, p.department, p.contact].filter(Boolean).join(" · ") || "—"}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="detail-field-label" style={{ marginBottom: 8, paddingTop: 14, borderTop: "1px solid var(--border-color)" }}>
            Witnesses
          </div>
          {witnesses.length === 0 ? (
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>None recorded.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {witnesses.map((w, i) => (
                <div key={i} className="person-row">
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{w.name}</div>
                  {w.statement && (
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4, lineHeight: 1.5 }}>&ldquo;{w.statement}&rdquo;</div>
                  )}
                  {w.contact && <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{w.contact}</div>}
                </div>
              ))}
            </div>
          )}
        </Card>

        {hasNotes && (
          <Card icon="📝" title="Actions & Notes">
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {d.immediate_actions && (
                <div>
                  <div className="detail-field-label">🚨 Immediate Actions Taken</div>
                  <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0", lineHeight: 1.6 }}>{d.immediate_actions}</p>
                </div>
              )}
              {d.preventive_measures && (
                <div>
                  <div className="detail-field-label">🛡 Preventive Measures</div>
                  <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0", lineHeight: 1.6 }}>{d.preventive_measures}</p>
                </div>
              )}
              {d.additional_comments && (
                <div>
                  <div className="detail-field-label">📌 Additional Comments</div>
                  <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0 0", lineHeight: 1.6 }}>{d.additional_comments}</p>
                </div>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
