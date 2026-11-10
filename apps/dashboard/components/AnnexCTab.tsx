"use client";

import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, CheckCircle2, Eraser, FileSpreadsheet, FileText, ListChecks, PenLine, RotateCcw, Save, Unlock } from "lucide-react";
import { pdfDownloadUrl, xlsxDownloadUrl, type ReportDetail, type SignOffRequest } from "@/lib/api";
import { reopenReportAction, saveAnnexCAction, signOffReportAction } from "@/app/reports/actions";
import { useCurrentUser } from "@/components/CurrentUser";
import { formatSgd } from "@/lib/pastReports";
import { annexCValues, type CaseStore } from "@/lib/caseState";
import { isBadAmount, sumRows } from "@/lib/annexRows";
import { LockedNote, Missing, SavedNote, UnsavedNote, Var, fieldInputStyle } from "./SaveNotes";
import { ReviewBadge } from "./ReviewBadge";
import { ANNEX_C_KEYS, annexCFieldState, openItemsLabel, openSections, openTotal, type OpenSection, type OpenTarget } from "@/lib/reviewRows";

const DEFAULT_TITLE = "Automotive Engineer Assessor";

/** A case tab an open item links to (components/StudioApp.tsx CaseTab). */
type OpenTab = Exclude<OpenTarget, "record">;

/** The sign-off box's "Open items" list (contract 7): each section with
 * fields still needing a person or a check, linked to where it is fixed --
 * its tab (?tab=, switched in place when the page passes onOpenTab), or the
 * Case record drawer for the parties. */
function OpenItemsList({
  reportId,
  sections,
  total,
  onOpenTab,
  onOpenRecord,
}: {
  reportId: string;
  sections: OpenSection[];
  total: number;
  onOpenTab?: (tab: OpenTab) => void;
  onOpenRecord?: () => void;
}) {
  const link = (s: OpenSection) => {
    if (s.target === "record") {
      return onOpenRecord ? (
        <button type="button" className="link-button" style={{ fontSize: 12 }} onClick={onOpenRecord}>
          {s.label}
        </button>
      ) : (
        <span>{s.label}</span>
      );
    }
    // Annex C's own figures are on this tab, just above.
    if (s.target === "annexC") return <span>{s.label}</span>;
    const tab = s.target;
    return (
      <a
        href={`/reports/${reportId}?tab=${tab}`}
        className="setup-help-link"
        onClick={(e) => {
          if (!onOpenTab || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
          e.preventDefault();
          onOpenTab(tab);
        }}
      >
        {s.label}
      </a>
    );
  };
  return (
    <div style={{ border: "1px solid var(--border-color)", borderRadius: 8, padding: "10px 12px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700 }}>
        <ListChecks aria-hidden style={{ width: 14, height: 14, color: total ? "var(--badge-amber-text)" : "var(--badge-green-text)" }} />
        {total ? `Open items (${total})` : "Nothing open: every field is filled in or confirmed"}
      </div>
      {sections.length > 0 && (
        <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          {sections.map((s) => {
            const Icon = s.needs ? PenLine : AlertTriangle;
            return (
              <li key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                <Icon
                  aria-label={s.needs ? "Needs you" : "Check"}
                  style={{ width: 12, height: 12, flexShrink: 0, color: s.needs ? "var(--badge-red-text)" : "var(--badge-amber-text)" }}
                />
                <span style={{ fontWeight: 600 }}>{link(s)}</span>
                <span>{s.text}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** [Reopen]: a signed-off case back to editable, which only the signer or
 * an admin may do (the API refuses anyone else). Shown in the header and
 * under the sign-off box. */
export function ReopenCaseButton({ reportId, style }: { reportId: string; style?: React.CSSProperties }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handle = async () => {
    if (busy || !confirm("Reopen this case? It goes back to a draft until it is signed off again.")) return;
    setBusy(true);
    setError(null);
    const res = await reopenReportAction(reportId);
    setBusy(false);
    if ("error" in res) {
      setError(res.error);
      return;
    }
    // The page's report prop refreshes; the store re-bases on it.
    router.refresh();
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <button type="button" className="btn-secondary-modern" style={style} disabled={busy} onClick={() => void handle()}>
        <Unlock style={{ width: 13, height: 13 }} /> {busy ? "Reopening…" : "Reopen"}
      </button>
      {error && <span style={{ color: "var(--danger, #ef4444)", fontSize: 12 }}>{error}</span>}
    </span>
  );
}

/** The report as the firm sends it. Unsigned, it prints as a draft. */
export function ReportDownloads({ reportId, anyDirty }: { reportId: string; anyDirty: boolean }) {
  return (
    <div className="report-downloads">
      {anyDirty && <span style={{ fontSize: 11, color: "var(--badge-amber-text)" }}>Save first: downloads use what is saved.</span>}
      <a href={pdfDownloadUrl(reportId)} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="The report with the photo pages">
        <FileText style={{ width: 13, height: 13 }} /> PDF
      </a>
      <a href={pdfDownloadUrl(reportId, { photos: false })} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="The four pages only">
        <FileText style={{ width: 13, height: 13 }} /> PDF without photos
      </a>
      <a href={xlsxDownloadUrl(reportId)} className="btn-secondary-modern" style={{ fontSize: 11, padding: "5px 12px" }} title="Cover Page, Annex A, Annex B and Annex C as a workbook">
        <FileSpreadsheet style={{ width: 13, height: 13 }} /> Excel
      </a>
    </div>
  );
}

/** Tab 5, Annex C: the recommendation letter with the live figures, the
 * contract lump sum and working days (PUT /reports/{id}/annex-c), the
 * sign-off box (POST /reports/{id}/sign-off), Reopen once signed, and the
 * download links. Moved from CaseAssessmentTab.tsx on 2026-11-04. */
export function AnnexCTab({
  report,
  store,
  canEdit,
  canSignOff,
  canReopen,
  isSignedOff,
  onOpenTab,
  onOpenRecord,
}: {
  report: ReportDetail;
  store: CaseStore;
  /** The case's permissions.edit, sign_off and reopen from the API. */
  canEdit: boolean;
  canSignOff: boolean;
  canReopen: boolean;
  isSignedOff: boolean;
  /** Opens another tab of the case in place (an open item's link). */
  onOpenTab?: (tab: OpenTab) => void;
  /** Opens the Case record drawer (the parties' open items). */
  onOpenRecord?: () => void;
}) {
  const router = useRouter();
  const { readOnly } = store;
  const d = store.data;
  const signOff = d.sign_off;
  const plate = d.vehicle_info?.plate_number || "";
  // The sign-off box only by someone who may sign off.
  const signOffLocked = isSignedOff || !canSignOff;

  // ---- Annex C --------------------------------------------------------------
  // The letter's two figures from the surveyor: contract lump sum and
  // working days. Empty until entered -- they used to default to S$8,700
  // and 11 days.
  const { lumpText, daysText } = store.annexC;
  const status = store.status.annexC;
  const { lumpValue, daysValue, badDays } = annexCValues(store.annexC);
  const annexCDirty = store.dirty.annexC;
  const annexCLocked = readOnly || status.save === "saving";

  const handleSaveAnnexC = async () => {
    if (annexCLocked || isBadAmount(lumpText) || badDays) return;
    store.setStatus("annexC", "saving");
    const res = await saveAnnexCAction(report.id, { agreed_lump_sum: lumpValue, repair_days: daysValue });
    if ("error" in res) {
      store.setStatus("annexC", "error", res.error);
      return;
    }
    store.applySaved("annexC", res);
    router.refresh();
  };

  const handleDiscardAnnexC = () => {
    if (annexCLocked) return;
    store.discard("annexC");
  };

  // The letter uses the tables as last saved -- what the PDF prints.
  const savedParts = sumRows(store.annexABaseline);
  const savedLabour = sumRows(store.annexBBaseline);
  const savedTotal = { est: Math.round((savedParts.est + savedLabour.est) * 100) / 100, adj: Math.round((savedParts.adj + savedLabour.adj) * 100) / 100 };
  const anyPriced = savedTotal.est > 0 || savedTotal.adj > 0;

  // ---- Sign-off -------------------------------------------------------------
  // Who signs is the signed-in user, with their own profile (Settings -> My
  // settings) -- the API takes it from the account. This defaulted to one
  // fixed surveyor's name, qualifications and licence, so every case read
  // as signed by them.
  const user = useCurrentUser();
  const signer = isSignedOff
    ? { name: signOff?.surveyor_name, qualifications: signOff?.qualifications, license: signOff?.license_number, firm: signOff?.firm_name }
    : { name: user?.name, qualifications: user?.qualifications, license: user?.license_number, firm: user?.firm_name };
  const surveyorName = signer.name || "";
  const qualifications = signer.qualifications || "";
  const licenseNumber = signer.license || "";
  const firmName = signer.firm || "";
  const noFullName = !isSignedOff && canSignOff && Boolean(user) && (!user?.name || user.name === user.username);
  const profileIncomplete = !isSignedOff && canSignOff && Boolean(user) && (!user?.license_number || noFullName);

  const [signatoryTitle, setSignatoryTitle] = useState<string>(signOff?.signatory_title || DEFAULT_TITLE);
  const [remarks, setRemarks] = useState<string>(signOff?.remarks || "");
  // Starts unticked -- the surveyor makes the declaration. A signed-off
  // case shows what was accepted.
  const [declared, setDeclared] = useState<boolean>(isSignedOff && signOff?.terms_accepted === true);
  const [isSigningOff, setIsSigningOff] = useState(false);
  const [signOffError, setSignOffError] = useState<string | null>(null);

  // ---- Open items (field review, contract 7) --------------------------------
  // What still needs a person or a check, from the case as saved (sign-off
  // takes it as saved). Owner decision 1: never a hard block -- a warning
  // and an extra tick, sent as open_items_ack. An older API sends no
  // review: then none of this shows and nothing new is sent.
  const review = isSignedOff ? null : report.review;
  const openList = review
    ? openSections(review, { damage: d.damage_summary || [], annexA: store.annexABaseline, annexB: store.annexBBaseline, annexC: store.annexCBaseline })
    : [];
  const localOpen = openTotal(openList);
  // The API's count when it refused a sign-off for open items (409), until
  // the case is fetched again.
  const [refusedOpen, setRefusedOpen] = useState<number | null>(null);
  useEffect(() => setRefusedOpen(null), [report.review]);
  const openN = refusedOpen ?? localOpen;
  const needAck = (Boolean(review) && openN > 0) || refusedOpen !== null;
  const [ackOpen, setAckOpen] = useState(false);
  // The tick is for a number of open items: a new number asks again.
  useEffect(() => setAckOpen(false), [openN]);

  // The signature pad. Only what was actually drawn is sent: an untouched
  // pad used to go out as a blank image.
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);
  const [hasSignature, setHasSignature] = useState(false);

  useEffect(() => {
    const ctx = canvasRef.current?.getContext("2d");
    if (ctx) {
      ctx.strokeStyle = "#1e3a8a";
      ctx.lineWidth = 2.5;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
    }
  }, [signOffLocked]);

  const padPoint = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    // The canvas may be shown narrower than its drawing size.
    const sx = e.currentTarget.width / rect.width;
    const sy = e.currentTarget.height / rect.height;
    return { x: (e.clientX - rect.left) * sx, y: (e.clientY - rect.top) * sy };
  };

  const handlePadDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = padPoint(e);
    // A new stroke starts where the pen lands (strokes used to join up).
    ctx.beginPath();
    ctx.moveTo(x, y);
    setIsDrawing(true);
  };

  const handlePadMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = padPoint(e);
    ctx.lineTo(x, y);
    ctx.stroke();
    setHasSignature(true);
  };

  const handleClearSignature = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasSignature(false);
  };

  // Reopened (signed off -> draft again): the declaration and the signature
  // start blank, not with the previous signer's tick and drawing.
  useEffect(() => {
    if (isSignedOff) return;
    setDeclared(false);
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasSignature(false);
  }, [isSignedOff]);

  const handleSignOffSubmit = async () => {
    if (!declared) {
      setSignOffError("Tick the declaration before signing off.");
      return;
    }
    if (needAck && !ackOpen) {
      setSignOffError(`${openN === 1 ? "1 item is" : `${openN} items are`} still open. Finish ${openN === 1 ? "it" : "them"}, or tick "${openItemsLabel(openN)}".`);
      return;
    }
    // Sign-off locks the case with the tables as saved; unsaved rows would be lost.
    const unsaved = store.unsavedSections();
    if (unsaved.length > 0 || store.anySaving) {
      setSignOffError(store.anySaving ? "Wait for the save to finish before signing off." : `Save or discard your changes to ${unsaved.join(", ")} before signing off.`);
      return;
    }

    setIsSigningOff(true);
    setSignOffError(null);

    const payload: SignOffRequest = {
      // Only what was drawn or typed here. The lump sum and working days
      // are not sent: Annex C saves them itself and the API keeps them.
      signature_data_url: hasSignature && canvasRef.current ? canvasRef.current.toDataURL("image/png") : undefined,
      signatory_title: signatoryTitle.trim() || DEFAULT_TITLE,
      remarks: remarks.trim() || undefined,
      // True only because the surveyor ticked it (checked above).
      terms_accepted: true,
      // Only when they ticked "Sign off with N items open" (never sent to
      // an older API, which shows no such tick).
      ...(needAck && ackOpen ? { open_items_ack: true } : {}),
    };

    const res = await signOffReportAction(report.id, payload);
    if ("error" in res) {
      if (res.openItems) {
        // Refused for open items: say how many, and offer the extra tick.
        const n = res.openItems.open;
        setRefusedOpen(n);
        setAckOpen(false);
        setSignOffError(`Not signed off: ${res.error} Finish ${n === 1 ? "it" : "them"} (see Open items), or tick "${openItemsLabel(n)}" and sign off again.`);
      } else {
        setSignOffError(res.error);
      }
      setIsSigningOff(false);
      return;
    }

    window.location.reload();
  };

  // ---- Letter values --------------------------------------------------------
  const workshopName = d.insurance_details?.workshop_assigned?.trim() || "";
  const letterLump = lumpValue;
  const letterDays = daysValue;

  return (
    <div className="card-glass" style={{ padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
        <div>
          <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Annex C : Recommendation</h4>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
            {plate ? `${plate} — ` : ""}The letter as the report prints it. Only the lump sum and the working days are typed here; the rest comes from the cover page and the
            annexes as saved.
          </div>
          <LockedNote isSignedOff={isSignedOff} canEdit={canEdit} />
        </div>
        <ReportDownloads reportId={report.id} anyDirty={store.anyDirty} />
      </div>

      <div className="annex-letter">
        <p>
          We have inspected thoroughly the actual damages found on the vehicle and our assessment is appended in the Annex(es) attached. The condition and age of the vehicle was
          considered before we reached our recommendation as to whether the parts need replacement or repair.
        </p>
        <p>
          {workshopName ? <Var>{workshopName}</Var> : <Missing>(Workshop not recorded — see the cover page)</Missing>} estimated repair cost is{" "}
          {anyPriced ? <Var>{formatSgd(savedTotal.est)}</Var> : <Missing>(not yet priced)</Missing>} and we have adjusted it to{" "}
          {anyPriced ? <Var>{formatSgd(savedTotal.adj)}</Var> : <Missing>(not yet priced)</Missing>} on a part by part basis. However, taking into consideration the age of the
          vehicle and the availability of used/recondition components. We, therefore recommend a contract lump sum repair cost.
        </p>
        <p>
          The repairer have agreed to undertake the repairs on a contract lump sum of {letterLump != null ? <Var>{formatSgd(letterLump)}</Var> : <Missing>(to be agreed)</Missing>},
          according to acceptable quality and standard. (Repairer discretion to repair parts, to replace with old parts, or to replace with OEM or genuine parts).
        </p>
        <p>
          The entire repair of the damaged vehicle should be completed within a reasonable period of {letterDays != null ? <Var>{letterDays}</Var> : <Missing>(to be agreed)</Missing>}{" "}
          working days.
        </p>
        <p>
          We have advised the repairer that the inspection was conducted strictly on a WITHOUT PREJUDICE basis and we have not given any instruction and authorisation to proceed
          with the repair of the vehicle.
        </p>
        <p>We are reverting the matter to you for your discretion and enclosed is our invoice for service rendered.</p>

        <div className="letter-closing">
          <p style={{ marginBottom: 4 }}>Yours faithfully,</p>
          {firmName && <p style={{ marginBottom: 4, fontWeight: 700, textTransform: "uppercase" }}>{firmName}</p>}
          {isSignedOff ? (
            <>
              {signOff?.signature_data_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={signOff.signature_data_url} alt="Signature" style={{ height: 60, display: "block", margin: "8px 0" }} />
              ) : (
                <div style={{ height: 40 }} />
              )}
              <p style={{ marginBottom: 2, fontWeight: 700, textTransform: "uppercase" }}>{surveyorName || "Not recorded"}</p>
              <p style={{ marginBottom: 2 }}>{signOff?.signatory_title || DEFAULT_TITLE}</p>
              {qualifications && <p style={{ marginBottom: 0 }}>{qualifications}</p>}
            </>
          ) : (
            <div className="letter-draft">DRAFT — not signed off</div>
          )}
        </div>
      </div>

      <div className="annex-c-inputs">
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Contract lump sum (S$)
            {review && <ReviewBadge state={annexCFieldState(lumpValue, review.fields?.[ANNEX_C_KEYS.lump])} review={review.fields?.[ANNEX_C_KEYS.lump]} label="Contract lump sum" />}
          </span>
          <input
            type="text"
            inputMode="decimal"
            value={lumpText}
            placeholder="Not recorded"
            aria-invalid={isBadAmount(lumpText) ? true : undefined}
            disabled={annexCLocked}
            onChange={(e) => store.setAnnexC({ lumpText: e.target.value })}
            style={{ ...fieldInputStyle, fontFamily: "var(--font-mono)", width: 160 }}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            Working days
            {review && <ReviewBadge state={annexCFieldState(daysValue, review.fields?.[ANNEX_C_KEYS.days])} review={review.fields?.[ANNEX_C_KEYS.days]} label="Working days" />}
          </span>
          <input
            type="text"
            inputMode="numeric"
            value={daysText}
            placeholder="Not recorded"
            aria-invalid={badDays ? true : undefined}
            disabled={annexCLocked}
            onChange={(e) => store.setAnnexC({ daysText: e.target.value })}
            style={{ ...fieldInputStyle, fontFamily: "var(--font-mono)", width: 100 }}
          />
        </label>
        {!readOnly && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginLeft: "auto" }}>
            <UnsavedNote state={status.save} dirty={annexCDirty} />
            <SavedNote state={status.save} dirty={annexCDirty} />
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!annexCDirty || annexCLocked} onClick={handleDiscardAnnexC}>
              <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
            </button>
            <button
              type="button"
              className="btn-primary-modern"
              style={{ fontSize: 11, padding: "6px 14px" }}
              disabled={!annexCDirty || annexCLocked || isBadAmount(lumpText) || badDays}
              onClick={() => void handleSaveAnnexC()}
            >
              <Save style={{ width: 13, height: 13 }} /> {status.save === "saving" ? "Saving…" : "Save Annex C"}
            </button>
          </div>
        )}
      </div>
      {status.save === "error" && status.error && (
        <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
          {status.error}
        </div>
      )}

      {/* ---- Sign-off ------------------------------------------------- */}
      <div className="signoff-box">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div>
            <h4 style={{ fontSize: 14, fontWeight: 800, margin: 0 }}>Sign-off</h4>
            <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
              {isSignedOff
                ? "Signed off. The report prints with the signature below."
                : "Signing off locks the case with the cover page and Annexes A to C as last saved, and prints the signature on Annex C."}
            </div>
          </div>
          {isSignedOff ? (
            <span className="chip-severity minor" style={{ fontSize: 11, display: "inline-flex", alignItems: "center", gap: 6 }}>
              <CheckCircle2 style={{ width: 14, height: 14 }} /> Signed off
              {signOff?.signature_date && <span suppressHydrationWarning>on {new Date(signOff.signature_date).toLocaleDateString("en-SG")}</span>}
            </span>
          ) : (
            <span className="chip-severity moderate" style={{ fontSize: 11 }}>
              Not signed off
            </span>
          )}
        </div>

        <div className="signoff-grid">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <div className="detail-field-label">Signed by</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: surveyorName ? "var(--text-primary)" : "var(--text-muted)" }}>{surveyorName || "Not recorded"}</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {qualifications || "No qualifications on the profile"}
                {licenseNumber ? ` · Licence ${licenseNumber}` : ""}
              </div>
            </div>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
              Title printed under the name
              <input
                type="text"
                value={isSignedOff ? signOff?.signatory_title || DEFAULT_TITLE : signatoryTitle}
                disabled={signOffLocked}
                onChange={(e) => setSignatoryTitle(e.target.value)}
                style={fieldInputStyle}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--text-muted)" }}>
              Remarks (kept on the case, not printed)
              <textarea
                value={isSignedOff ? signOff?.remarks || "" : remarks}
                placeholder={isSignedOff ? "None" : "Anything the next person should know"}
                rows={3}
                disabled={signOffLocked}
                onChange={(e) => setRemarks(e.target.value)}
                style={{ ...fieldInputStyle, resize: "vertical" }}
              />
            </label>
          </div>

          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <div className="detail-field-label">Signature</div>
              {!signOffLocked && (
                <button
                  type="button"
                  style={{ fontSize: 11, background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}
                  onClick={handleClearSignature}
                >
                  <Eraser style={{ width: 12, height: 12 }} /> Clear
                </button>
              )}
            </div>
            {!signOffLocked ? (
              <canvas
                ref={canvasRef}
                width={360}
                height={160}
                className="signature-canvas-pad"
                style={{ width: "100%", maxWidth: 360, background: "#ffffff" }}
                aria-label="Draw your signature"
                onMouseDown={handlePadDown}
                onMouseUp={() => setIsDrawing(false)}
                onMouseLeave={() => setIsDrawing(false)}
                onMouseMove={handlePadMove}
              />
            ) : (
              <div className="signature-canvas-pad" style={{ cursor: "default" }}>
                {/* A signature only once signed off -- this used to
                    render the surveyor's name as a cursive signature
                    on unsigned cases. */}
                {isSignedOff ? (
                  signOff?.signature_data_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={signOff.signature_data_url} alt="Signature" style={{ maxHeight: 120, maxWidth: "100%" }} />
                  ) : (
                    <div style={{ fontFamily: "Georgia, serif", fontStyle: "italic", fontSize: 22, color: "var(--text-primary)" }}>{surveyorName || "Signed"}</div>
                  )
                ) : (
                  <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Not signed.</div>
                )}
              </div>
            )}
            {!signOffLocked && (
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                Draw with the mouse. Left blank, the report prints the name and title without a signature image.
              </div>
            )}
          </div>
        </div>

        {review && <OpenItemsList reportId={report.id} sections={openList} total={localOpen} onOpenTab={onOpenTab} onOpenRecord={onOpenRecord} />}

        <label className="compliance-check-item">
          <input type="checkbox" checked={declared} disabled={signOffLocked} onChange={(e) => setDeclared(e.target.checked)} />
          <span>I inspected the vehicle, and Annexes A to C are my assessment of the actual damage found, made strictly on a WITHOUT PREJUDICE basis; no repair was authorised.</span>
        </label>

        {needAck && !signOffLocked && (
          <label className="compliance-check-item" style={{ marginTop: 6 }}>
            <input type="checkbox" checked={ackOpen} onChange={(e) => setAckOpen(e.target.checked)} />
            <span>
              <strong>{openItemsLabel(openN)}</strong>: blank fields print as &quot;Not recorded&quot;, and values nobody checked print as they stand.
            </span>
          </label>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10, borderTop: "1px solid var(--border-color)", flexWrap: "wrap", gap: 10 }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
            {isSignedOff ? "This case is locked. Reopen it to change anything." : "Save every tab first: sign-off takes the case as saved."}
          </div>

          {signOffError && <div style={{ color: "var(--danger, #ef4444)", fontSize: 12 }}>{signOffError}</div>}

          {profileIncomplete && (
            <div style={{ color: "var(--badge-amber-text)", fontSize: 12 }}>
              {noFullName ? "Your profile has no full name yet, so the report will print your username." : "Your profile has no licence number, so the report will print without one."}{" "}
              <a href="/settings#my-profile" className="setup-help-link">
                Add it in Settings
              </a>
            </div>
          )}

          {isSignedOff ? (
            canReopen ? (
              <ReopenCaseButton reportId={report.id} />
            ) : (
              <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Only the signer or an admin can reopen it.</div>
            )
          ) : !canSignOff ? (
            <div style={{ color: "var(--badge-amber-text)", fontSize: 12 }}>Your account can&apos;t sign off reports yet. Ask an admin to sign it off, or to allow you.</div>
          ) : (
            <button type="button" className="btn-primary-modern" style={{ padding: "10px 24px", fontSize: 13 }} disabled={isSigningOff} onClick={() => void handleSignOffSubmit()}>
              <CheckCircle2 style={{ width: 16, height: 16 }} />
              {isSigningOff ? "Signing off…" : "Sign off"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
