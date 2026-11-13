"use client";

import { useEffect, type ReactNode } from "react";
import { Bot, Clock, FileText, MapPin, Pencil, Shield, Users, X } from "lucide-react";
import { aiKeySourceLabel, aiRunErrorLabel, aiRunKindLabel, channelLabel, PUBLIC_CHANNEL, type AiRunSummary, type ReportDetail } from "@/lib/api";
import { formatSgtDateTime } from "@/lib/caseFields";
import { fieldView, keysInState, PARTY_REVIEW_KEYS, valueAt, type CaseReview, type FieldView } from "@/lib/fieldReview";
import { PublicSenderCard } from "@/components/inbox/PublicSenderCard";
import { ConfirmAiButton, FieldReviewControls } from "@/components/FieldReviewControls";
import { ReviewProgress } from "@/components/ReviewProgress";

const NOT_RECORDED = "Not recorded";

// The AI run log (round 3, B3): every Gemini call made on this case or its
// draft, as the API recorded it. Nothing here is worked out from the case
// data; a fact the row doesn't carry is left out of the line.

/** Who ran it when no account did (the bot's own runs, the server's). */
const AI_RUN_CHANNEL_WHO: Record<string, string> = {
  dashboard: "the website",
  telegram: "the Telegram bot",
  whatsapp: "WhatsApp",
  system: "the server",
};

/** "P03, P07, P12" when few enough to name, else "14 photos"; null when
 * the row doesn't say (a redraft or price lookup sends none). */
function runPhotos(run: AiRunSummary): string | null {
  const sent = run.photos_sent;
  if (Array.isArray(sent)) {
    if (sent.length === 0) return null;
    if (sent.length <= 6) return sent.join(", ");
    return `${sent.length} photos`;
  }
  const n = sent && typeof sent === "object" && typeof sent.count === "number" ? sent.count : run.photos_count;
  if (typeof n !== "number") return null;
  return `${n} photo${n === 1 ? "" : "s"}`;
}

/** How long it took, from the row's own timestamps; null while it runs. */
function runSeconds(run: AiRunSummary): string | null {
  if (!run.finished_at) return null;
  const ms = Date.parse(run.finished_at) - Date.parse(run.started_at);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return `${Math.round(ms / 1000)} s`;
}

/** The outcome as a short label, and the scrubbed error (or the code in
 * words) for a run that failed. */
function runOutcome(run: AiRunSummary): { label: string; tone: "ok" | "danger" | "warn" | ""; error: string | null } {
  switch (run.status) {
    case "ok":
      return { label: "OK", tone: "ok", error: null };
    case "running":
      return { label: "Running…", tone: "", error: null };
    case "aborted":
      return { label: "Stopped", tone: "warn", error: run.error_text?.trim() || aiRunErrorLabel(run.error_code) };
    case "failed":
    case "refused":
      return {
        label: run.status === "refused" ? "Refused" : "Failed",
        tone: "danger",
        // The API scrubs error_text of digits and plates before storing it.
        error: run.error_text?.trim() || aiRunErrorLabel(run.error_code) || null,
      };
    default:
      return { label: run.status, tone: "", error: null };
  }
}

/** One labelled fact of the case record: the recorded value, or "Not
 * recorded" -- never a guessed one. `review` (field review): its badge
 * and buttons, beside the label. */
function DossierField({ label, value, mono, review }: { label: string; value?: string | number | null; mono?: boolean; review?: ReactNode }) {
  const has = value !== undefined && value !== null && String(value).trim() !== "";
  return (
    <div className="dossier-field-cell">
      {review ? (
        <div className="dossier-field-label review-label-row">
          <span>{label}</span>
          {review}
        </div>
      ) : (
        <div className="dossier-field-label">{label}</div>
      )}
      <div className={`dossier-field-value${mono && has ? " mono" : ""}`} style={has ? undefined : { color: "var(--text-muted)", fontWeight: 500 }}>
        {has ? String(value) : NOT_RECORDED}
      </div>
    </div>
  );
}

const HISTORY_LABELS: Record<string, string> = {
  created: "Created",
  edited: "Edited",
  // Detail: "confirmed 3 fields" / "unconfirmed 1 field" (contract 7). Its own
  // action: "edited" entries are merged within 30 minutes, which lost the count.
  fields_confirmed: "Fields confirmed",
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
  // The parts-catalogue look-up (app/api/catalogue.py). Detail: "3 part(s)"
  // asked about / "was running".
  catalogue_lookup_requested: "Catalogue look-up requested",
  catalogue_lookup_cancelled: "Catalogue look-up cancelled",
  // A PDF or Excel made on download (detail: pdf / pdf-no-photos / xlsx);
  // a preview with no stored PDF yet renders one too, hence not "sent".
  exported: "Report downloaded",
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
  fieldReview,
}: {
  report: ReportDetail;
  open: boolean;
  onClose: () => void;
  isSignedOff: boolean;
  canEdit: boolean;
  /** Field review (contract 7): badges, and Confirm / N/A for whoever may
   * edit the case. Without a review, nothing shows. */
  fieldReview?: CaseReview;
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
  // Newest first from the API (at most 50). An older API sends none.
  const aiRuns = report.ai_runs || [];

  // Field review: the drawer edits nothing, so each fact's state is the
  // review's (with this person's Confirm / N/A shown at once).
  const review = isSignedOff ? null : (fieldReview?.review ?? null);
  const canReview = Boolean(fieldReview && canEdit && !isSignedOff);
  const views: Record<string, FieldView> = {};
  if (review) for (const key of [...Object.values(PARTY_REVIEW_KEYS), "incident_datetime"]) views[key] = fieldView(review, key, valueAt(d, key), valueAt(d, key));
  const badge = (key: string, label: string) =>
    review ? (
      <FieldReviewControls
        view={views[key]}
        label={label}
        onReview={canReview && fieldReview ? (k, action) => void fieldReview.apply([k], action) : undefined}
        busy={fieldReview?.saving}
      />
    ) : undefined;
  const K = PARTY_REVIEW_KEYS;

  return (
    <>
      <div className="record-drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="record-drawer" role="dialog" aria-modal="true" aria-label="Case record">
        <div className="record-drawer-head">
          <div>
            <h3>Case record</h3>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>What the report doesn&apos;t print. Every value is as recorded.</div>
            {review && (
              <div className="review-bar" style={{ margin: "6px 0 0" }}>
                <ReviewProgress review={review} sections={["parties"]} />
                {canReview && fieldReview && (
                  <ConfirmAiButton keys={keysInState(views, "ai")} busy={fieldReview.saving} onConfirm={(keys) => void fieldReview.apply(keys, "confirm")} />
                )}
              </div>
            )}
            {fieldReview?.error && (
              <div className="setup-error" role="alert" style={{ margin: "6px 0 0" }}>
                {fieldReview.error}
              </div>
            )}
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
              <DossierField label="Accident date" value={d.incident_datetime} review={badge("incident_datetime", "Accident date")} />
            </div>
          </section>

          <section>
            <div className="record-section-title">
              <MapPin style={{ width: 12, height: 12 }} /> Location and description
            </div>
            <div className="record-grid" style={{ marginBottom: 8 }}>
              <DossierField label="Location" value={d.location} review={badge(K.location, "Location")} />
              <DossierField label="Type of accident" value={d.accident_type} review={badge(K.accident_type, "Type of accident")} />
            </div>
            <div className="dossier-field-label" style={{ marginBottom: 4 }}>Description (AI draft, as recorded)</div>
            <p className="record-note">{d.description?.trim() || NOT_RECORDED}</p>
          </section>

          <section>
            <div className="record-section-title">
              <Shield style={{ width: 12, height: 12 }} /> Police, insurer and other party
            </div>
            <div className="record-grid">
              <DossierField label="Reported to police" value={reportedToPolice} review={badge(K.reported_to_police, "Reported to police")} />
              <DossierField
                label="Police report no."
                value={pol?.report_number || pol?.reference_number}
                mono
                review={badge(K.report_number, "Police report no.")}
              />
              <DossierField label="Station" value={pol?.police_station} review={badge(K.police_station, "Station")} />
              <DossierField label="Officer" value={pol?.officer_name} review={badge(K.officer_name, "Officer")} />
              <DossierField label="Date reported" value={pol?.date_reported} review={badge(K.date_reported, "Date reported")} />
              <DossierField label="Insurer" value={ins?.insurer_name} review={badge(K.insurer_name, "Insurer")} />
              <DossierField label="Policy no." value={ins?.policy_number} mono review={badge(K.policy_number, "Policy no.")} />
              {/* The insurer's claim number only -- never a generated reference. */}
              <DossierField label="Claim no." value={ins?.claim_number} mono review={badge(K.claim_number, "Claim no.")} />
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

          {/* AI RUNS (round 3, B3) -- every Gemini call on this case or its
              draft: what for, when, which photos, which model, how long, how
              it ended, who pressed it and on which key. Phase 2 adds a
              "Retry on tab 1" link beside a failed redetect. */}
          <section>
            <div className="record-section-title">
              <Bot style={{ width: 12, height: 12 }} /> AI runs ({aiRuns.length})
            </div>
            {aiRuns.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>None recorded</div>
            ) : (
              <ul className="ai-run-list">
                {aiRuns.map((run) => {
                  const outcome = runOutcome(run);
                  const who = run.actor_name || AI_RUN_CHANNEL_WHO[run.channel] || run.channel;
                  const facts = [runPhotos(run), run.model_used, runSeconds(run), `by ${who}`, aiKeySourceLabel(run.key_source, run.key_last4)].filter(
                    Boolean
                  );
                  return (
                    <li key={run.id} className="ai-run-row">
                      <div className="ai-run-head">
                        <strong>{aiRunKindLabel(run.kind)}</strong>
                        <span className={`ai-run-status ${outcome.tone}`}>{outcome.label}</span>
                        {typeof run.parts_found === "number" && (
                          <span style={{ color: "var(--text-secondary)" }}>
                            {run.parts_found} part{run.parts_found === 1 ? "" : "s"} found
                          </span>
                        )}
                        <span className="ai-run-when" suppressHydrationWarning>
                          {formatSgtDateTime(run.started_at)}
                        </span>
                      </div>
                      <div className="ai-run-meta">{facts.join(" · ")}</div>
                      {outcome.error && <div className="ai-run-error">{outcome.error}</div>}
                    </li>
                  );
                })}
              </ul>
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
