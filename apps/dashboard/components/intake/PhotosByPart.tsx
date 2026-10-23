"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ImagePlus, Plus } from "lucide-react";
import { fileUrl, type DamageSummaryItem } from "@/lib/api";
import type { IntakePhoto } from "@/lib/intake";
import { groupPhotosByPart, isEvidencePhoto, type PhotoGroup } from "@/lib/intakeReview";

/** Thumbnails shown per group before the "+N" tile. */
const MAX_THUMBS = 6;

type Props = {
  photos: IntakePhoto[];
  damage: DamageSummaryItem[];
  readPhotoIds: string[];
  /** Adds a damage item for the group's part ("" for an area-only group,
   * where the surveyor has to choose the part) linked to `photoId`. */
  onAddDamage: (part: string, photoId: string) => void;
  /** Links `photoId` to the listed item at `itemIndex`, which has no photo. */
  onAttachPhoto: (itemIndex: number, photoId: string) => void;
  onOpenPhoto: (group: PhotoGroup, photoId: string) => void;
};

/** Cross-checks the damage list against the photo sort: every part the
 * sorter saw in the photos, and whether the list has it. */
export function PhotosByPart({ photos, damage, readPhotoIds, onAddDamage, onAttachPhoto, onOpenPhoto }: Props) {
  const groups = useMemo(() => groupPhotosByPart(photos, damage), [photos, damage]);
  const readIds = useMemo(() => new Set(readPhotoIds), [readPhotoIds]);
  const toCheck = groups.filter((g) => !g.listed && g.damageCount > 0).length;
  const anyDamage = groups.some((g) => g.damageCount > 0);
  // Decided once, so the panel doesn't fold away under the surveyor's
  // cursor as the last unlisted part is added.
  const [open, setOpen] = useState(toCheck > 0);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  if (!photos.some(isEvidencePhoto)) return null;

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>
          <button type="button" className="rv-collapse" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? <ChevronDown style={{ width: 16, height: 16 }} /> : <ChevronRight style={{ width: 16, height: 16 }} />}
            Photos by part
          </button>
        </h2>
        {toCheck > 0 ? (
          <span className="intake-chip warn">
            {toCheck} with damage photos not listed
          </span>
        ) : anyDamage ? (
          <span className="intake-chip ok">All parts with damage photos listed</span>
        ) : null}
      </div>

      {open &&
        (groups.length === 0 ? (
          <p className="intake-muted rv-parts-intro">None of the photos have been sorted by part yet.</p>
        ) : (
          <>
            <p className="intake-muted rv-parts-intro">
              From the photo sort. Parts with damage photos that aren&apos;t in the list are shown first — check them.
            </p>
            <div className="rv-part-groups">
              {groups.map((g) => {
                const showAll = expanded.has(g.id) || g.photos.length <= MAX_THUMBS;
                // The "+N" tile takes the last slot when the group overflows.
                const shown = showAll ? g.photos : g.photos.slice(0, MAX_THUMBS - 1);
                const hidden = g.photos.length - shown.length;
                const attention = !g.listed && g.damageCount > 0;
                return (
                  <div key={g.id} className={`rv-part-group ${attention ? "attention" : ""}`}>
                    <div className="rv-part-group-head">
                      <span className="rv-part-name">{g.title}</span>
                      <span className={`intake-chip ${g.listed ? "ok" : attention ? "warn" : ""}`}>{g.listed ? "Listed" : "Not listed"}</span>
                      <span className="intake-muted">
                        {g.photos.length} photo{g.photos.length === 1 ? "" : "s"}
                        {g.damageCount > 0 ? ` · ${g.damageCount} show${g.damageCount === 1 ? "s" : ""} damage` : ""}
                      </span>
                      <span className="rv-part-actions">
                        {!g.listed && (
                          <button
                            type="button"
                            className={`${attention ? "btn-primary-modern" : "btn-secondary-modern"} rv-small-btn`}
                            onClick={() => onAddDamage(g.part ?? "", g.best.id)}
                            title={g.part ? `Add ${g.part} to the damage list, with ${g.best.label}` : `Add a damage item with ${g.best.label} — then choose the part`}
                          >
                            <Plus style={{ width: 13, height: 13 }} /> Add as damage
                          </button>
                        )}
                        {g.listed && g.itemWithoutPhoto !== null && (
                          <button
                            type="button"
                            className="btn-secondary-modern rv-small-btn"
                            onClick={() => onAttachPhoto(g.itemWithoutPhoto as number, g.best.id)}
                            title={`Link ${g.best.label} to the listed ${g.part}, which has no photo`}
                          >
                            <ImagePlus style={{ width: 13, height: 13 }} /> Attach photo
                          </button>
                        )}
                      </span>
                    </div>
                    <div className="rv-part-thumbs">
                      {shown.map((p) => (
                        <button
                          key={p.id}
                          type="button"
                          className={`rv-thumb ${readIds.has(p.id) ? "read" : ""}`}
                          onClick={() => onOpenPhoto(g, p.id)}
                          title={p.note ? `${p.label} — ${p.note}` : p.label}
                          aria-label={`View ${p.label}${p.shows_damage ? ", shows damage" : ""}${p.note ? `: ${p.note}` : ""}`}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={fileUrl(p.thumb_url)} alt="" loading="lazy" />
                          <span className="rv-thumb-label">{p.label}</span>
                          {p.shows_damage && <span className="rv-damage-dot" />}
                        </button>
                      ))}
                      {hidden > 0 && (
                        <button
                          type="button"
                          className="rv-thumb rv-thumb-more"
                          onClick={() => setExpanded((s) => new Set(s).add(g.id))}
                          aria-label={`Show ${hidden} more photo${hidden === 1 ? "" : "s"}`}
                        >
                          +{hidden}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="intake-muted rv-parts-legend">
              <span className="rv-damage-dot" /> shows damage <span className="rv-read-dot" /> read by the AI · hover a photo for the sorter&apos;s note
            </p>
          </>
        ))}
    </section>
  );
}
