"use client";

import { useEffect } from "react";
import { Clock, FileText, MapPin, Pencil, Shield, Users, X } from "lucide-react";
import { channelLabel, PUBLIC_CHANNEL, type ReportDetail } from "@/lib/api";
import { formatSgtDateTime } from "@/lib/caseFields";
import { PublicSenderCard } from "@/components/inbox/PublicSenderCard";

const NOT_RECORDED = "Not recorded";

/** One labelled fact of the case record: the recorded value, or "Not
 * recorded" -- never a guessed one. */
function DossierField({ label, value, mono }: { label: string; value?: string | number | null; mono?: boolean }) {
  const has = value !== undefined && value !== null && String(value).trim() !== "";
  return (
    <div className="dossier-field-cell">
      <div className="dossier-field-label">{label}</div>
      <div className={`dossier-field-value${mono && has ? " mono" : ""}`} style={has ? undefined : { color: "var(--text-muted)", fontWeight: 500 }}>
        {has ? String(value) : NOT_RECORDED}
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
  // Detail: "Assigned to Name (@username)" or "Unassigned".
  assigned: "Owner changed",
  // Reports from the public (release P6). The detail carries the reference
  // (and a discard's reason), never the sender's name or phone.
  taken: "Taken from the Public Inbox",
  returned_to_inbox: "Returned to the Public Inbox",
  discarded: "Discarded",
  expired: "Deleted: not taken within 30 days",
  withdrawn: "Withdrawn by the sender",
  withdraw_requested: "Sender asked to withdraw it",
  // The five tabs and their saves (PR #21 and the 5-tab studio).
  cover_page_edited: "Cover page saved",
  annex_a_edited: "Annex A saved",
  annex_a_prefilled: "Annex A priced from past cases",
  annex_b_edited: "Annex B saved",
  annex_c_edited: "Annex C saved",
  damage_item_edited: "Damaged part edited",
  damage_item_added: "Damaged part added",
  damage_item_removed: "Damaged part removed",
};

/** The Case record drawer: what the report never prints -- who sent it, how
 * it came in, where, the police and insurer facts, the other party, people,
 * witnesses, and the case history. Read-only; [Edit record] goes to the
 * edit form. Cut down from CaseFileTab.tsx on 2026-11-04: the "Tier" cards,
 * the completion meter, the tyre matrix with its LTA verdict, the SLA, the
 * claim archetype, GST / BOLA lines and the eleven permanently empty cells
 * went; the vehicle, dates, static check, tyres and point of impact live on
 * the Cover page tab now. Opened from the case header; a full-screen sheet
 * on a phone. */
export function CaseRecordDrawer({
  report,
  open,
  onClose,
  isSignedOff,
  canEdit,
}: {
  report: ReportDetail;
  open: boolean;
  onClose: () => void;
  isSignedOff: boolean;
  canEdit: boolean;
}) {
  // Esc closes; the page behind stops scrolling while the sheet is open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  const d = report.data;
  const ins = d.insurance_details;
  const pol = d.police_report;
  const tp = d.third_party_info;
  const people = d.people_involved || [];
  const witnesses = d.witnesses || [];
  // Sent by a member of the public through the Telegram bot (release P6).
  const isPublic = report.channel === PUBLIC_CHANNEL;
  const publicRef = report.public_submission?.reference ?? null;
  // Only a recorded "yes" is a fact; a default-false flag is not "No".
  const reportedToPolice = pol?.reported_to_police || d.reported_to_authorities ? "Yes" : null;
  const history = report.history || [];

  return (
    <>
      <div className="record-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="record-drawer" role="dialog" aria-modal="true" aria-label="Case record">
        <div className="record-drawer-head">
          <div>
            <h3>Case record</h3>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>What the report doesn&apos;t print. Every value is as recorded.</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {canEdit && !isSignedOff && (
              <a href={`/reports/${report.id}/edit`} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }}>
                <Pencil style={{ width: 12, height: 12 }} /> Edit record →
              </a>
            )}
            <button type="button" className="record-drawer-close" onClick={onClose} title="Close (Esc)" aria-label="Close">
              <X style={{ width: 15, height: 15 }} />
            </button>
          </div>
        </div>

        <div className="record-drawer-body">
          {/* Who sent it, on a report from the public (release P6). Filed, so
              it can't go back to the inbox from here. */}
          {report.public_submission && <PublicSenderCard reportId={report.id} info={report.public_submission} showAiCheck={!isSignedOff} />}

          <section>
            <div className="record-section-title">
              <FileText style={{ width: 12, height: 12 }} /> Intake
            </div>
            <div className="record-grid">
              <DossierField label="Filed" value={formatSgtDateTime(report.created_at)} mono />
              <DossierField label="Channel" value={isPublic ? `${channelLabel(report.channel)}${publicRef ? ` · ${publicRef}` : ""}` : channelLabel(report.channel)} />
              {/* The case's reporter as recorded -- not the firm's surveyor. On a
                  report from the public, the member of the public who sent it. */}
              <DossierField label="Reporter" value={[d.reporter_name, d.reporter_role].filter(Boolean).join(" · ")} />
              <DossierField label="Reporter contact" value={d.reporter_contact || d.reporter_email} mono />
              <DossierField label="Company" value={d.company_name || d.reporter_department} />
              <DossierField label="Accident date" value={d.incident_datetime} />
            </div>
          </section>

          <section>
            <div className="record-section-title">
              <MapPin style={{ width: 12, height: 12 }} /> Location and description
            </div>
            <div className="record-grid" style={{ marginBottom: 8 }}>
              <DossierField label="Location" value={d.location} />
              <DossierField label="Type of accident" value={d.accident_type} />
            </div>
            <div className="dossier-field-label" style={{ marginBottom: 4 }}>Description (AI draft, as recorded)</div>
            <p className="record-note">{d.description?.trim() || NOT_RECORDED}</p>
          </section>

          <section>
            <div className="record-section-title">
              <Shield style={{ width: 12, height: 12 }} /> Police, insurer and other party
            </div>
            <div className="record-grid">
              <DossierField label="Reported to police" value={reportedToPolice} />
              <DossierField label="Police report no." value={pol?.report_number || pol?.reference_number} mono />
              <DossierField label="Station" value={pol?.police_station} />
              <DossierField label="Officer" value={pol?.officer_name} />
              <DossierField label="Date reported" value={pol?.date_reported} />
              <DossierField label="Insurer" value={ins?.insurer_name} />
              <DossierField label="Policy no." value={ins?.policy_number} mono />
              {/* The insurer's claim number only -- never a generated reference. */}
              <DossierField label="Claim no." value={ins?.claim_number} mono />
              <DossierField label="Other vehicle" value={tp?.plate_number} mono />
              <DossierField label="Other vehicle model" value={tp?.make_model} />
              <DossierField label="Other driver" value={tp?.driver_name} />
              <DossierField label="Other driver contact" value={tp?.driver_contact} mono />
              <DossierField label="Other insurer" value={tp?.insurer_name} />
              <DossierField label="Other party damage" value={tp?.damage_description} />
            </div>
          </section>

          <section>
            <div className="record-section-title">
              <Users style={{ width: 12, height: 12 }} /> People and witnesses
            </div>
            {people.length === 0 && witnesses.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>None recorded</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {people.map((p, i) => (
                  <div key={`p-${i}`} style={{ padding: 8, background: "var(--surface-elevated)", borderRadius: 6, fontSize: 12 }}>
                    <strong>{p.name || NOT_RECORDED}</strong>
                    {p.role ? <span style={{ color: "var(--text-secondary)" }}> · {p.role}</span> : null}
                    {p.department ? <span style={{ color: "var(--text-secondary)" }}> · {p.department}</span> : null}
                    {p.contact ? <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}> · {p.contact}</span> : null}
                  </div>
                ))}
                {witnesses.map((w, i) => (
                  <div key={`w-${i}`} style={{ padding: 8, background: "var(--surface-elevated)", borderRadius: 6, fontSize: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontWeight: 700 }}>
                      <span>Witness: {w.name || NOT_RECORDED}</span>
                      <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-muted)", fontWeight: 500 }}>{w.contact || ""}</span>
                    </div>
                    {w.statement && (
                      <p style={{ color: "var(--text-secondary)", fontStyle: "italic", margin: "4px 0 0" }}>&ldquo;{w.statement}&rdquo;</p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* CASE HISTORY -- who created, edited, signed off and reopened it
              (recorded by the API since sign-in was added). */}
          <section>
            <div className="record-section-title">
              <Clock style={{ width: 12, height: 12 }} /> Case history
            </div>
            {history.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>No history recorded</div>
            ) : (
              <ul className="case-history">
                {history.map((h, i) => (
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
            )}
          </section>

          <div className="record-remark">
            Remark printed on every report: &quot;This inspection was conducted entirely on a &quot;WITHOUT PREJUDICE&quot; basis and we have not authorised any
            repair.&quot;
          </div>
        </div>
      </aside>
    </>
  );
}
