/** One store per case page (components/StudioApp.tsx): the report as the
 * server last sent it, one draft per section the tabs edit (cover page,
 * Annex A, Annex B, Annex C), and the dirty / saving / error state of each.
 * The tabs read and write through it so a save on one tab (Annex A) can
 * move what another shows (Annex B's Summary, Annex C's letter) at once,
 * and so one guard covers every unsaved change. The rules were moved from
 * CaseAssessmentTab.tsx on 2026-11-04, not rewritten. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AnnexAInfo,
  AnnexAPartItem,
  AnnexBInfo,
  AnnexBLabourItem,
  AnnexCInfo,
  CoverPageResponse,
  DamageItemsResponse,
  ReportData,
  ReportDetail,
  TyreCondition,
} from "./api";
import { coverFormFrom, text, type CoverForm } from "./coverForm";
import {
  annexSignature,
  fromAnnexRow,
  fromLabourRow,
  labourSignature,
  parseAmount,
  toAnnexRow,
  toLabourRow,
  type AnnexRow,
  type LabourRow,
} from "./annexRows";

export type CaseSection = "cover" | "annexA" | "annexB" | "annexC";
export type SaveState = "idle" | "saving" | "saved" | "error";
export type SectionStatus = { save: SaveState; error: string | null };

/** The two Annex C figures as typed, so "8,700" and "11 " survive typing. */
export type AnnexCDraft = { lumpText: string; daysText: string };

/** How a section is named in a message ("Save or discard your changes to
 * the cover page, Annex A before signing off"). */
export const SECTION_NAMES: Record<CaseSection, string> = {
  cover: "the cover page",
  annexA: "Annex A",
  annexB: "Annex B",
  annexC: "Annex C",
};

const SECTIONS: CaseSection[] = ["cover", "annexA", "annexB", "annexC"];

type State = {
  data: ReportData;
  cover: CoverForm;
  coverBaseline: CoverForm;
  /** The tyres as saved, so a save keeps what the form doesn't show (condition). */
  tyresSaved: TyreCondition | null;
  annexA: AnnexRow[];
  annexABaseline: AnnexAPartItem[];
  /** The server's Annex A rows changed (a part was changed on tab 1) while
   * Annex A had unsaved lines: the draft was left alone, Discard shows the
   * new rows. */
  annexAChangedUnderDraft: boolean;
  annexB: LabourRow[];
  annexBBaseline: AnnexBLabourItem[];
  annexC: AnnexCDraft;
  annexCBaseline: AnnexCInfo;
  status: Record<CaseSection, SectionStatus>;
};

const EMPTY_ANNEX_C: AnnexCInfo = { workshop_total: 0, adjusted_total: 0 };

function annexCDraftFrom(c: AnnexCInfo): AnnexCDraft {
  return { lumpText: text(c.agreed_lump_sum), daysText: text(c.repair_days) };
}

/** The lump sum and working days as the letter and the save read them:
 * blank is null (not recorded), a bad number is flagged, never silently 0. */
export function annexCValues(draft: AnnexCDraft): { lumpValue: number | null; daysValue: number | null; badDays: boolean } {
  const lumpValue = draft.lumpText.trim() === "" ? null : parseAmount(draft.lumpText);
  const daysParsed = parseInt(draft.daysText.trim(), 10);
  const daysValue = draft.daysText.trim() === "" ? null : Number.isFinite(daysParsed) ? daysParsed : null;
  const badDays = draft.daysText.trim() !== "" && !/^\d+$/.test(draft.daysText.trim());
  return { lumpValue, daysValue, badDays };
}

function initialState(data: ReportData): State {
  const cover = coverFormFrom(data);
  const annexA = data.annex_a?.items || [];
  const annexB = data.annex_b?.items || [];
  const annexC = data.annex_c ?? EMPTY_ANNEX_C;
  const idle = (): SectionStatus => ({ save: "idle", error: null });
  return {
    data,
    cover,
    coverBaseline: cover,
    tyresSaved: data.vehicle_info?.tyres ?? null,
    annexA: annexA.map(toAnnexRow),
    annexABaseline: annexA,
    annexAChangedUnderDraft: false,
    annexB: annexB.map(toLabourRow),
    annexBBaseline: annexB,
    annexC: annexCDraftFrom(annexC),
    annexCBaseline: annexC,
    status: { cover: idle(), annexA: idle(), annexB: idle(), annexC: idle() },
  };
}

/** Whether a section's draft differs from what was last loaded or saved.
 * Nothing counts as unsaved on a case that can't be edited. */
function isDirty(s: State, section: CaseSection, readOnly: boolean): boolean {
  if (readOnly) return false;
  switch (section) {
    case "cover":
      return JSON.stringify(s.cover) !== JSON.stringify(s.coverBaseline);
    case "annexA":
      return annexSignature(s.annexA.map(fromAnnexRow)) !== annexSignature(s.annexABaseline);
    case "annexB":
      return labourSignature(s.annexB.map(fromLabourRow)) !== labourSignature(s.annexBBaseline);
    case "annexC": {
      const { lumpValue, daysValue } = annexCValues(s.annexC);
      return lumpValue !== (s.annexCBaseline.agreed_lump_sum ?? null) || daysValue !== (s.annexCBaseline.repair_days ?? null);
    }
  }
}

/** Annex C as the API saved it, after any annex is saved. The totals move;
 * the lump sum and days being typed stay unless the Annex C draft is clean
 * (a Save Annex A used to wipe unsaved Annex C typing without a word). */
function takeAnnexC(s: State, saved: AnnexCInfo, readOnly: boolean): State {
  const keepTyping = isDirty(s, "annexC", readOnly);
  return { ...s, annexCBaseline: saved, annexC: keepTyping ? s.annexC : annexCDraftFrom(saved) };
}

export interface ApplySaved {
  (section: "cover", res: CoverPageResponse): void;
  (section: "annexA", res: { annex_a: AnnexAInfo; annex_c?: AnnexCInfo }): void;
  (section: "annexB", res: { annex_b: AnnexBInfo; annex_c?: AnnexCInfo }): void;
  (section: "annexC", res: { annex_c: AnnexCInfo }): void;
}

export type CaseStore = {
  /** The report's data as the server last sent or saved it. */
  data: ReportData;
  readOnly: boolean;
  cover: CoverForm;
  coverBaseline: CoverForm;
  tyresSaved: TyreCondition | null;
  annexA: AnnexRow[];
  annexABaseline: AnnexAPartItem[];
  annexAChangedUnderDraft: boolean;
  annexB: LabourRow[];
  annexBBaseline: AnnexBLabourItem[];
  annexC: AnnexCDraft;
  annexCBaseline: AnnexCInfo;
  status: Record<CaseSection, SectionStatus>;
  dirty: Record<CaseSection, boolean>;
  anyDirty: boolean;
  anySaving: boolean;
  setCover: (patch: Partial<CoverForm>) => void;
  setAnnexA: (update: (rows: AnnexRow[]) => AnnexRow[]) => void;
  setAnnexB: (update: (rows: LabourRow[]) => LabourRow[]) => void;
  setAnnexC: (patch: Partial<AnnexCDraft>) => void;
  setStatus: (section: CaseSection, save: SaveState, error?: string | null) => void;
  /** The API's answer to a save replaces that section's draft and baseline
   * (its numbering, adjusted costs, dropped blank lines), and the Annex C
   * totals any answer carries. */
  applySaved: ApplySaved;
  /** After PATCH / POST / DELETE damage-items: the parts and the Annex A
   * the API re-synced to them. An unsaved Annex A draft is left alone. */
  applyDamageItems: (res: DamageItemsResponse) => void;
  /** Back to the section as last loaded or saved. */
  discard: (section: CaseSection) => void;
  /** The sections with unsaved changes, in words, for a guard message. */
  unsavedSections: () => string[];
};

/** The case page's store. `report` is the server's latest; when it changes
 * (router.refresh after a save, a reopen) only clean sections are re-based
 * on it, so nothing typed is lost. */
export function useCaseState(report: ReportDetail, readOnly: boolean): CaseStore {
  const [s, setS] = useState<State>(() => initialState(report.data));

  // A fresh report from the server: server truth and the clean sections
  // follow it; a section with unsaved changes keeps its draft and baseline.
  const seen = useRef(report);
  useEffect(() => {
    if (seen.current === report) return;
    seen.current = report;
    setS((prev) => {
      const fresh = initialState(report.data);
      const next: State = { ...prev, data: report.data };
      if (!isDirty(prev, "cover", readOnly)) {
        next.cover = fresh.cover;
        next.coverBaseline = fresh.coverBaseline;
        next.tyresSaved = fresh.tyresSaved;
      }
      // Rows that didn't change keep their React keys, so the inputs don't
      // remount (and lose focus) on every router.refresh().
      if (!isDirty(prev, "annexA", readOnly)) {
        const same = annexSignature(fresh.annexABaseline) === annexSignature(prev.annexABaseline);
        next.annexA = same ? prev.annexA : fresh.annexA;
        next.annexABaseline = same ? prev.annexABaseline : fresh.annexABaseline;
        next.annexAChangedUnderDraft = false;
      }
      if (!isDirty(prev, "annexB", readOnly)) {
        const same = labourSignature(fresh.annexBBaseline) === labourSignature(prev.annexBBaseline);
        next.annexB = same ? prev.annexB : fresh.annexB;
        next.annexBBaseline = same ? prev.annexBBaseline : fresh.annexBBaseline;
      }
      if (!isDirty(prev, "annexC", readOnly)) {
        next.annexC = fresh.annexC;
        next.annexCBaseline = fresh.annexCBaseline;
      }
      return next;
    });
  }, [report, readOnly]);

  const dirty = useMemo<Record<CaseSection, boolean>>(
    () => ({
      cover: isDirty(s, "cover", readOnly),
      annexA: isDirty(s, "annexA", readOnly),
      annexB: isDirty(s, "annexB", readOnly),
      annexC: isDirty(s, "annexC", readOnly),
    }),
    [s, readOnly]
  );
  const anyDirty = SECTIONS.some((k) => dirty[k]);
  const anySaving = SECTIONS.some((k) => s.status[k].save === "saving");

  // Leaving the page (reload, another URL) with unsaved changes asks first.
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);

  const setCover = useCallback((patch: Partial<CoverForm>) => setS((prev) => ({ ...prev, cover: { ...prev.cover, ...patch } })), []);
  const setAnnexA = useCallback((update: (rows: AnnexRow[]) => AnnexRow[]) => setS((prev) => ({ ...prev, annexA: update(prev.annexA) })), []);
  const setAnnexB = useCallback((update: (rows: LabourRow[]) => LabourRow[]) => setS((prev) => ({ ...prev, annexB: update(prev.annexB) })), []);
  const setAnnexC = useCallback((patch: Partial<AnnexCDraft>) => setS((prev) => ({ ...prev, annexC: { ...prev.annexC, ...patch } })), []);

  const setStatus = useCallback(
    (section: CaseSection, save: SaveState, error: string | null = null) =>
      setS((prev) => ({ ...prev, status: { ...prev.status, [section]: { save, error } } })),
    []
  );

  const applySaved = useCallback(
    ((section: CaseSection, res: unknown) => {
      setS((prev) => {
        const saved = (prev2: State): State => ({ ...prev2, status: { ...prev2.status, [section]: { save: "saved", error: null } } });
        switch (section) {
          case "cover": {
            const r = res as CoverPageResponse;
            const form = coverFormFrom(r);
            return saved({
              ...prev,
              data: {
                ...prev.data,
                report_id: r.report_id,
                incident_datetime: r.incident_datetime,
                cover_page: r.cover_page,
                vehicle_info: r.vehicle_info,
                insurance_details: r.insurance_details,
              },
              cover: form,
              coverBaseline: form,
              tyresSaved: r.vehicle_info?.tyres ?? null,
            });
          }
          case "annexA": {
            const r = res as { annex_a: AnnexAInfo; annex_c?: AnnexCInfo };
            const items = r.annex_a?.items || [];
            let next: State = {
              ...prev,
              data: { ...prev.data, annex_a: r.annex_a, ...(r.annex_c ? { annex_c: r.annex_c } : {}) },
              annexA: items.map(toAnnexRow),
              annexABaseline: items,
              annexAChangedUnderDraft: false,
            };
            if (r.annex_c) next = takeAnnexC(next, r.annex_c, readOnly);
            return saved(next);
          }
          case "annexB": {
            const r = res as { annex_b: AnnexBInfo; annex_c?: AnnexCInfo };
            const items = r.annex_b?.items || [];
            let next: State = {
              ...prev,
              data: { ...prev.data, annex_b: r.annex_b, ...(r.annex_c ? { annex_c: r.annex_c } : {}) },
              annexB: items.map(toLabourRow),
              annexBBaseline: items,
            };
            if (r.annex_c) next = takeAnnexC(next, r.annex_c, readOnly);
            return saved(next);
          }
          case "annexC": {
            const r = res as { annex_c: AnnexCInfo };
            // Saved from this section, so the inputs show what was kept.
            return saved({
              ...prev,
              data: { ...prev.data, annex_c: r.annex_c },
              annexCBaseline: r.annex_c,
              annexC: annexCDraftFrom(r.annex_c),
            });
          }
        }
      });
    }) as ApplySaved,
    [readOnly]
  );

  const applyDamageItems = useCallback(
    (res: DamageItemsResponse) => {
      setS((prev) => {
        const items = res.annex_a?.items || prev.annexABaseline;
        const annexADirty = isDirty(prev, "annexA", readOnly);
        let next: State = {
          ...prev,
          data: {
            ...prev.data,
            damage_summary: res.damage_summary,
            // Kept in step, or a removed last part would come back through
            // the page's damaged_parts fallback and the next Remove 404.
            damaged_parts: res.damaged_parts ?? res.damage_summary.map((i) => i.part),
            ...(res.annex_a ? { annex_a: res.annex_a } : {}),
            ...(res.annex_c ? { annex_c: res.annex_c } : {}),
          },
          annexABaseline: items,
          // Unsaved Annex A lines are never overwritten by the machine: the
          // new rows wait behind Discard.
          annexA: annexADirty ? prev.annexA : items.map(toAnnexRow),
          annexAChangedUnderDraft: annexADirty && annexSignature(items) !== annexSignature(prev.annexABaseline),
        };
        if (res.annex_c) next = takeAnnexC(next, res.annex_c, readOnly);
        return next;
      });
    },
    [readOnly]
  );

  const discard = useCallback((section: CaseSection) => {
    setS((prev) => {
      const next: State = { ...prev, status: { ...prev.status, [section]: { save: "idle", error: null } } };
      switch (section) {
        case "cover":
          next.cover = prev.coverBaseline;
          break;
        case "annexA":
          next.annexA = prev.annexABaseline.map(toAnnexRow);
          next.annexAChangedUnderDraft = false;
          break;
        case "annexB":
          next.annexB = prev.annexBBaseline.map(toLabourRow);
          break;
        case "annexC":
          next.annexC = annexCDraftFrom(prev.annexCBaseline);
          break;
      }
      return next;
    });
  }, []);

  const unsavedSections = useCallback(() => SECTIONS.filter((k) => dirty[k]).map((k) => SECTION_NAMES[k]), [dirty]);

  return {
    data: s.data,
    readOnly,
    cover: s.cover,
    coverBaseline: s.coverBaseline,
    tyresSaved: s.tyresSaved,
    annexA: s.annexA,
    annexABaseline: s.annexABaseline,
    annexAChangedUnderDraft: s.annexAChangedUnderDraft,
    annexB: s.annexB,
    annexBBaseline: s.annexBBaseline,
    annexC: s.annexC,
    annexCBaseline: s.annexCBaseline,
    status: s.status,
    dirty,
    anyDirty,
    anySaving,
    setCover,
    setAnnexA,
    setAnnexB,
    setAnnexC,
    setStatus,
    applySaved,
    applyDamageItems,
    discard,
    unsavedSections,
  };
}
