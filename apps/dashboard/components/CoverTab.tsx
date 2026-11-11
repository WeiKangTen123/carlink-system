"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { RotateCcw, Save } from "lucide-react";
import type { DamageSummaryItem, ReportDetail } from "@/lib/api";
import { saveCoverPageAction } from "@/app/reports/actions";
import { isAdmin, useCurrentUser } from "@/components/CurrentUser";
import { aiReadFrom, coverFormFrom, coverRequestFrom } from "@/lib/coverForm";
import type { CaseStore } from "@/lib/caseState";
import { coverViews, keysInState, withStates, type CaseReview } from "@/lib/fieldReview";
import { CoverPageFields } from "./CoverPageFields";
import { ConfirmAiButton } from "./FieldReviewControls";
import { ReviewProgress } from "./ReviewProgress";
import { ImpactSketch } from "./ImpactSketch";
import { LockedNote, SavedNote, UnsavedNote } from "./SaveNotes";

/** Tab 2, Cover page: the firm's six sections (components/CoverPageFields.tsx),
 * the impact sketch under section 5, and Save / Discard. Owns report_id,
 * incident_datetime, cover_page, vehicle_info and insurance_details.claim_type
 * / workshop_assigned through PUT /reports/{id}/cover-page. Moved from
 * CaseAssessmentTab.tsx on 2026-11-04. */
export function CoverTab({
  report,
  store,
  canEdit,
  isSignedOff,
  damageEntries,
  fieldReview,
}: {
  report: ReportDetail;
  store: CaseStore;
  canEdit: boolean;
  isSignedOff: boolean;
  damageEntries: DamageSummaryItem[];
  /** Field review (contract 7): the case's review and its Confirm / N/A
   * (lib/fieldReview.ts useCaseReview). Without a review, nothing shows. */
  fieldReview?: CaseReview;
}) {
  const router = useRouter();
  const user = useCurrentUser();
  const { readOnly, cover, coverBaseline, tyresSaved } = store;
  const status = store.status.cover;
  const coverDirty = store.dirty.cover;
  const coverLocked = readOnly || status.save === "saving";
  // Our Ref is the firm's file number: the API only lets staff who may
  // sign off set it, so the field is read-only for everyone else.
  const ourRefEditable = isAdmin(user) || Boolean(user?.may_sign_off);
  // "(P03)" beside a field while it still says what the AI read.
  const ai = useMemo(() => aiReadFrom(report.ai_read, report.ai_read_photos), [report.ai_read, report.ai_read_photos]);
  // Field review: each field's state against the report the review was
  // worked out from, so a field changed and not yet saved shows as yours.
  const reviewed = fieldReview?.review ?? null;
  const reviewBase = useMemo(() => coverFormFrom(report.data), [report.data]);
  const views = useMemo(() => coverViews(reviewed, cover, reviewBase), [reviewed, cover, reviewBase]);
  const shownReview = useMemo(() => (reviewed ? withStates(reviewed, views) : null), [reviewed, views]);
  const canReview = Boolean(fieldReview && !readOnly);

  const handleSave = async () => {
    if (coverLocked) return;
    store.setStatus("cover", "saving");
    const res = await saveCoverPageAction(report.id, coverRequestFrom(cover, coverBaseline, tyresSaved));
    if ("error" in res) {
      store.setStatus("cover", "error", res.error);
      return;
    }
    // The API's answer replaces the form, so what is shown is what was kept.
    store.applySaved("cover", res);
    // The plate and workshop show elsewhere on the page too.
    router.refresh();
  };

  const handleDiscard = () => {
    if (coverLocked || !confirm("Discard your unsaved changes to the cover page?")) return;
    store.discard("cover");
  };

  return (
    <div className="card-glass" style={{ padding: 20 }}>
      <div style={{ marginBottom: 14 }}>
        <h4 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Accident Vehicle Inspection / Assessment Report</h4>
        <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>
          The cover page as the firm prints it. Dates are printed as typed. Anything left blank prints as &quot;Not recorded&quot;.
        </div>
        <LockedNote isSignedOff={isSignedOff} canEdit={canEdit} />
      </div>

      {shownReview && !isSignedOff && (
        <div className="review-bar">
          <ReviewProgress review={shownReview} sections={["cover"]} />
          {canReview && fieldReview && (
            <ConfirmAiButton keys={keysInState(views, "ai")} busy={fieldReview.saving || coverLocked} onConfirm={(keys) => void fieldReview.apply(keys, "confirm")} />
          )}
        </div>
      )}
      {fieldReview?.error && (
        <div className="setup-error" role="alert" style={{ marginBottom: 12 }}>
          {fieldReview.error}
        </div>
      )}

      <CoverPageFields
        value={cover}
        onChange={(patch) => {
          if (!coverLocked) store.setCover(patch);
        }}
        disabled={coverLocked}
        ourRefEditable={ourRefEditable}
        aiRead={ai.tags}
        aiValues={ai.values}
        showUsualValues={!readOnly}
        review={views}
        onReview={canReview && fieldReview ? (key, action) => void fieldReview.apply([key], action) : undefined}
        reviewBusy={fieldReview?.saving}
        sketch={<ImpactSketch damageEntries={damageEntries} pointOfImpact={cover.point_of_impact} />}
      />

      {!readOnly && (
        <div className="annex-edit-bar">
          <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 14 }}>
            <UnsavedNote state={status.save} dirty={coverDirty} />
            <SavedNote state={status.save} dirty={coverDirty} />
            <button type="button" className="btn-secondary-modern" style={{ fontSize: 11, padding: "6px 12px" }} disabled={!coverDirty || coverLocked} onClick={handleDiscard}>
              <RotateCcw style={{ width: 13, height: 13 }} /> Discard changes
            </button>
            <button type="button" className="btn-primary-modern" style={{ fontSize: 11, padding: "6px 14px" }} disabled={!coverDirty || coverLocked} onClick={() => void handleSave()}>
              <Save style={{ width: 13, height: 13 }} /> {status.save === "saving" ? "Saving…" : "Save cover page"}
            </button>
          </div>
          {status.save === "error" && status.error && (
            <div className="setup-error" style={{ marginTop: 10, marginBottom: 0 }}>
              {status.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
