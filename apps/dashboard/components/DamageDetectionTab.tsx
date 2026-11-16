"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fileUrl,
  type AnnexAPartItem,
  type DamageItemsResponse,
  type DamageSummaryItem,
  type DecideAction,
  type DecideResult,
  type ReportData,
  type ReportDetail,
} from "@/lib/api";
import { addDamageItemAction } from "@/app/reports/actions";
import { buildFindings, type Finding, type PhotoInfo, type PhotoLookup } from "@/lib/findings";
import { indexLabel, labelIndex } from "@/lib/damageLinks";
import { useDamageSelection } from "@/lib/useDamageSelection";
import { suggestionKey, useSuggestionDecisions } from "@/lib/useSuggestionDecisions";
import { detectBodyType, partForZone, resolveZones } from "@/lib/vehicleZones";
import { DamageMapPanel, revealDamageMap } from "./DamageMapPanel";
import { DetectedPartsTable } from "./DetectedPartsTable";
import { EvidenceViewer } from "./EvidenceViewer";
import { SuggestionPanel } from "./SuggestionPanel";

// Moved to DamageMapPanel with the 3D map it guards; kept importable from here.
export { BlueprintErrorBoundary } from "./DamageMapPanel";

/** "27 Feb, 10:16" in Singapore time, for the one-line status strip (the
 * Case record drawer has the full date). h23 for the same reason as
 * caseFields.formatSgtDateTime: Node and browsers disagree on midnight
 * under hour12:false. */
function formatSgtShort(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-SG", {
    timeZone: "Asia/Singapore",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

interface Props {
  report: ReportDetail;
  /** The case as the page's store holds it now (fresh after every change). */
  data: ReportData;
  isSignedOff: boolean;
  canEdit: boolean;
  /** The detected parts as the store holds them (fresh after every change). */
  damageEntries: DamageSummaryItem[];
  vehicleName: string;
  photos: string[];
  photoThumbs?: string[];
  photoPreviews?: string[];
  /** Annex A as saved, for the "n lines, m priced" cell per part. */
  annexAItems: AnnexAPartItem[];
  onDamageItemsApplied: (res: DamageItemsResponse) => void;
  onGoToAnnexA: () => void;
}

/** The viewer's state: which findings it pages through, from where. */
type ViewerState = { mode: "suggestions" | "listed" | "photo"; startKey: string | null; startPhoto: string | null };

/** A key from a suggestion card is the suggestion's own key or its finding
 * key; the viewer and the map take the finding key. */
const asFindingKey = (key: string) => (key.startsWith("sug:") || key.startsWith("item:") ? key : `sug:${key}`);

/** Tab 1, Damage detection: the 3D damage map (with its photo, chips and
 * strip), the suggestions still to decide and the detected parts table --
 * all views of the SAME findings, sharing one selection (useDamageSelection)
 * so a part picked anywhere lights up everywhere, and one evidence viewer.
 * Owns damage_summary and the photo links; never shows a price. From
 * CaseInspectionTab.tsx, 2026-11-04; the map replaced the blueprint |
 * photos split in contract 9. */
export function DamageDetectionTab({
  report,
  data,
  isSignedOff,
  canEdit,
  damageEntries,
  vehicleName,
  photos,
  photoThumbs,
  photoPreviews,
  annexAItems,
  onDamageItemsApplied,
  onGoToAnnexA,
}: Props) {
  const readOnly = isSignedOff || !canEdit;
  const { selection, setSelection, hover, setHover, itemsRemoved } = useDamageSelection();
  const [viewer, setViewer] = useState<ViewerState | null>(null);
  // Suggestions the person has opened in the viewer: deciding one later,
  // from a card or the map, still counts as "looked" (as on the wizard).
  const lookedKeys = useRef(new Set<string>());
  const openViewer = useCallback((v: ViewerState) => {
    if (v.mode === "suggestions" && v.startKey) lookedKeys.current.add(suggestionKey(v.startKey));
    setViewer(v);
  }, []);

  // The suggestions are the server's (decisions are stamped there); a
  // decision's answer replaces them here, and a refreshed case resets them.
  const [secondLook, setSecondLook] = useState(report.data.second_look ?? null);
  useEffect(() => setSecondLook(report.data.second_look ?? null), [report]);

  // Same badge numbers as the 3D map's markers and the table's rows.
  const zoneResolutions = useMemo(() => resolveZones(damageEntries), [damageEntries]);

  // Every finding, dismissed suggestions too: the map's "Show dismissed"
  // and the suggestion panel's decided list need them.
  const findings = useMemo(
    () => buildFindings({ ...data, damage_summary: damageEntries, second_look: secondLook }, { showDismissed: true }),
    [data, damageEntries, secondLook],
  );
  const suggestions = useMemo(() => findings.filter((f) => f.kind === "suggestion"), [findings]);

  // The case's photos by P-number: the 1600px preview to look at, the
  // thumbnail for strips, the sorter's area from filing's photo index.
  const photoIndex = data.photo_index;
  const photoInfos = useMemo<PhotoInfo[]>(
    () =>
      photos.map((url, i) => ({
        ref: indexLabel(i),
        label: indexLabel(i),
        src: fileUrl(photoPreviews?.[i] || url),
        thumb: fileUrl(photoThumbs?.[i] || photoPreviews?.[i] || url),
        area: photoIndex?.[i]?.area ?? null,
      })),
    [photos, photoThumbs, photoPreviews, photoIndex],
  );
  const lookup = useCallback<PhotoLookup>((ref) => photoInfos[labelIndex(ref)] ?? null, [photoInfos]);
  const photoOrder = useMemo(() => photoInfos.map((p) => p.ref), [photoInfos]);
  const photoAreas = useMemo(() => Object.fromEntries(photoInfos.map((p) => [p.ref, p.area ?? null])), [photoInfos]);

  const { decide } = useSuggestionDecisions({
    surface: "case",
    reportId: report.id,
    onSaved: (r: DecideResult, asked) => {
      setSecondLook(r.second_look);
      // /reports answers with the parts and Annex A too, as POST /damage-items does.
      const withItems = r as DecideResult & Partial<DamageItemsResponse>;
      if (Array.isArray(withItems.damage_summary)) onDamageItemsApplied(withItems as unknown as DamageItemsResponse);
      // An undone add took its item off the server's list (the list now
      // shown): a selection or hover of a row below it follows its row up.
      if (asked?.action === "undo" && r.item && typeof r.item_index === "number") itemsRemoved([r.item_index]);
    },
  });
  // The map sends a suggestion's own key, the cards and viewer "sug:<key>".
  const decideLooked = useCallback(
    (key: string, action: DecideAction, looked: boolean) => decide(key, action, looked || lookedKeys.current.has(suggestionKey(key))),
    [decide],
  );

  // The table's highlighted row: the selected item, or the first listed
  // part on a selected zone.
  const selectedIndex = useMemo(() => {
    if (selection?.kind === "finding") return selection.key.startsWith("item:") ? Number(selection.key.slice("item:".length)) : null;
    if (selection?.kind === "zone") {
      const idx = zoneResolutions.findIndex((r) => r?.keys.includes(selection.zoneKey));
      return idx >= 0 ? idx : null;
    }
    return null;
  }, [selection, zoneResolutions]);

  const showOnMap = (key: string) => {
    setViewer(null);
    setSelection({ kind: "finding", key: asFindingKey(key) });
    revealDamageMap();
  };

  const addFromMap = async (zoneKey: string) => {
    const part = partForZone(zoneKey);
    if (!part || readOnly) return;
    if (!confirm(`Add "${part}" to the detected parts?`)) return;
    const res = await addDamageItemAction(report.id, { part, other_part_name: null, condition: null, remarks: null, photo_reference: null });
    if ("error" in res) alert(res.error);
    else onDamageItemsApplied(res);
  };

  const viewerFindings: Finding[] = useMemo(() => {
    if (!viewer) return [];
    if (viewer.mode === "suggestions") return suggestions;
    if (viewer.mode === "listed") return findings.filter((f) => f.kind === "item");
    return findings.filter((f) => f.status !== "dismissed");
  }, [viewer, suggestions, findings]);

  // The last AI read as the run log recorded it (round 3, B3): which photos
  // went and when. Null when the AI never read this case (filled in by
  // hand, or filed before the log existed), and absent from an older API.
  const lastRead = report.ai_last_read ?? null;
  const partsCount = damageEntries.length;
  const verifiedCount = damageEntries.filter((e) => e.human_verified).length;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* What the AI read and found. The parts are counted from the record
          as it is now (the surveyor may have added or removed some since);
          the read itself comes from the run log, so with no log entry the
          line falls back to counts alone, with no time invented. */}
      <div className="detection-status-strip">
        <span suppressHydrationWarning>
          {lastRead ? (
            <>
              AI read {lastRead.photos_read} of {plural(lastRead.photos_total, "photo")} on {formatSgtShort(lastRead.at)}
              {typeof lastRead.seconds === "number" ? ` (${Math.round(lastRead.seconds)} s)` : ""}. {plural(partsCount, "part")} found,{" "}
              {verifiedCount} verified.
            </>
          ) : (
            <>
              {plural(partsCount, "part")} detected from {plural(photos.length, "photo")}.
            </>
          )}
        </span>
        {/* Phase 2: [Add photos] (POST /reports/{id}/photos) and [Re-run
            detection] (POST /reports/{id}/redetect) go here, with the
            redetect status line. Not built in this PR. */}
      </div>

      <DamageMapPanel
        surface="case"
        findings={findings}
        photos={lookup}
        photoOrder={photoOrder}
        photoAreas={photoAreas}
        bodyType={detectBodyType(vehicleName, data.vehicle_info?.body_type)}
        selection={selection}
        onSelect={setSelection}
        hover={hover}
        onHover={setHover}
        onOpenEvidence={(key, photo) =>
          openViewer({ mode: key.startsWith("sug:") ? "suggestions" : key ? "listed" : "photo", startKey: key || null, startPhoto: photo ?? null })
        }
        onDecide={readOnly ? undefined : (key: string, action: DecideAction) => decideLooked(key, action, false)}
        onAddDamage={readOnly ? undefined : (zoneKey) => void addFromMap(zoneKey)}
        readOnly={readOnly}
      />

      {/* The suggestions stay on the case until sign-off, then read-only. */}
      {secondLook && (
        <SuggestionPanel
          surface="case"
          findings={suggestions}
          decisions={secondLook.decisions ?? []}
          check={secondLook.check ?? null}
          photos={lookup}
          readOnly={readOnly}
          onDecide={decideLooked}
          onLook={(key) => openViewer({ mode: "suggestions", startKey: asFindingKey(key), startPhoto: null })}
          onShowOnMap={showOnMap}
        />
      )}

      {/* Full width: the table needs the room for its columns */}
      <DetectedPartsTable
        reportId={report.id}
        readOnly={readOnly}
        damageEntries={damageEntries}
        zoneResolutions={zoneResolutions}
        highlightedDamageIndex={selectedIndex}
        onHotspotClick={(idx) => setSelection({ kind: "finding", key: `item:${idx}` })}
        photos={photos}
        photoThumbs={photoThumbs}
        onSelectPhoto={(idx) => {
          setSelection({ kind: "photo", ref: indexLabel(idx) });
          revealDamageMap();
        }}
        annexAItems={annexAItems}
        onApplied={onDamageItemsApplied}
        // Indices shift when a row goes, so a selected row is dropped
        // rather than left on the wrong part.
        onRemoved={() => {
          if (selection?.kind === "finding" && selection.key.startsWith("item:")) setSelection(null);
        }}
        onGoToAnnexA={onGoToAnnexA}
        review={isSignedOff ? null : report.review}
        onShowEvidence={(idx) => setViewer({ mode: "listed", startKey: `item:${idx}`, startPhoto: null })}
        onShowOnMap={(idx) => showOnMap(`item:${idx}`)}
      />

      <EvidenceViewer
        open={viewer !== null}
        mode={viewer?.mode ?? "listed"}
        findings={viewerFindings}
        startKey={viewer?.startKey ?? null}
        startPhoto={viewer?.startPhoto ?? null}
        photos={lookup}
        onDecide={readOnly ? undefined : decideLooked}
        onShowOnMap={showOnMap}
        onClose={() => setViewer(null)}
        readOnly={readOnly}
      />
    </div>
  );
}
