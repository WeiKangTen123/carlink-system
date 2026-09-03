"use client";

import dynamic from "next/dynamic";
import { type ReportDetail, type DamageSummaryItem } from "@/lib/api";
import type { ZoneResolution } from "@/lib/vehicleZones";
import { severityClass } from "./StudioApp";
import { CaseEvidenceTab } from "./CaseEvidenceTab";
import { CaseDamageTab } from "./CaseDamageTab";

// Three.js/WebGL only exists client-side -- SSR-rendering the Canvas would
// either crash on the server or produce a hydration mismatch, so this is
// loaded only after mount.
const VehicleBlueprint3D = dynamic(
  () => import("@/components/VehicleBlueprint3D").then((m) => m.VehicleBlueprint3D),
  { ssr: false, loading: () => <div className="blueprint-stage-3d blueprint-3d-loading">Loading 3D blueprint&hellip;</div> }
);

interface Props {
  report: ReportDetail;
  damageEntries: DamageSummaryItem[];
  zoneResolutions: (ZoneResolution | null)[];
  vehicleName: string;
  photos: string[];
  activePhotoIndex: number;
  highlightedDamageIndex: number | null;
  onSelectPhoto: (idx: number) => void;
  onHotspotClick: (idx: number, item: DamageSummaryItem) => void;
}

/** The three views of a case's damage -- 3D blueprint, evidence photos,
 * and the parts checklist -- are all views of the SAME damage_summary
 * data, and reviewing a case means constantly cross-referencing between
 * them ("where is it → what does it look like → is it on the list").
 *
 * They were briefly split across three separate tabs, which forced
 * clicking a blueprint part to silently jump you to a different tab just
 * to show the photo it selected. That tab-switch was the tell: these are
 * linked views, not separate sections, so they live on one screen and
 * cross-highlight instead. Tabs are still used for the genuinely
 * different mode (sign-off/paperwork), just not to separate these three.
 */
export function CaseInspectionTab({
  report,
  damageEntries,
  zoneResolutions,
  vehicleName,
  photos,
  activePhotoIndex,
  highlightedDamageIndex,
  onSelectPhoto,
  onHotspotClick,
}: Props) {
  const d = report.data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Top row: the two visual views, side by side, always both visible */}
      <div className="inspection-split">
        <div className="card-glass">
          <div className="card-header">
            <div>
              <div className="card-title">
                <span>📐</span> Vehicle Body Blueprint
              </div>
              <div className="card-subtitle">
                Click a marker to highlight it in the photo and parts list below
              </div>
            </div>
            <span className={`chip-severity ${severityClass(d.severity_level)}`}>
              {damageEntries.length} Damaged {damageEntries.length === 1 ? "Zone" : "Zones"}
            </span>
          </div>

          <VehicleBlueprint3D
            damageEntries={damageEntries}
            onHotspotClick={onHotspotClick}
            highlightedDamageIndex={highlightedDamageIndex}
            vehicleName={vehicleName}
          />
        </div>

        <CaseEvidenceTab
          photos={photos}
          damageEntries={damageEntries}
          activePhotoIndex={activePhotoIndex}
          onSelectPhoto={onSelectPhoto}
        />
      </div>

      {/* Full width: the checklist needs the room for its six columns */}
      <CaseDamageTab
        damageEntries={damageEntries}
        zoneResolutions={zoneResolutions}
        highlightedDamageIndex={highlightedDamageIndex}
        onHotspotClick={onHotspotClick}
        estimatedRepairCost={d.insurance_details?.estimated_repair_cost}
      />
    </div>
  );
}
