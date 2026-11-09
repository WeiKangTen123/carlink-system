"use client";

import { useMemo, useState } from "react";
import { Plus, X } from "lucide-react";
import { fileUrl, type DamageSummaryItem, type ReportData } from "@/lib/api";
import { photoForReference, type IntakePhoto } from "@/lib/intake";

/** One part the second look suggests checking (bot-service
 * reports/schema.SecondLookItem). Never a finding until a person ticks it. */
export type SecondLookItem = {
  part: string;
  /** The firm's wording for the part. */
  name?: string | null;
  /** "@<photo id>" while a draft. */
  photo_reference?: string | null;
  note?: string | null;
  condition_tokens?: string[];
  condition?: string | null;
  damage_type?: string | null;
  confidence?: string | null;
  /** "kit": past cases of the same make had it with a part found here;
   * "nearby": next to a part found here. */
  source?: string | null;
  /** Damage-list names that mean the part is listed already. */
  covers?: string[];
};

/** data.second_look (bot-service reports/schema.SecondLook), written after
 * each AI reading of the draft (app/ai/second_look.py). */
export type SecondLook = {
  status: "ok" | "skipped" | "failed" | (string & {});
  model?: string | null;
  note?: string | null;
  /** "past Honda Vezel cases" when the firm's history supplied candidates. */
  scope?: string | null;
  seconds?: number | null;
  photos_sent?: number;
  candidates?: number;
  items?: SecondLookItem[];
  /** Parts a person dismissed: never shown again, also after a re-read. */
  dismissed?: string[];
};

type DataWithLook = ReportData & { second_look?: SecondLook | null };

type Props = {
  data: ReportData;
  onChange: (update: (d: ReportData) => ReportData) => void;
  photos: IntakePhoto[];
};

/** The damage item a tick adds: what a person adding the part makes
 * (checked), with the suggestion's photo and its firm words as the
 * condition, so Annex A treats it like any other part they listed. */
export function itemFromSuggestion(s: SecondLookItem): DamageSummaryItem {
  return {
    part: s.part,
    damage_type: s.damage_type ?? null,
    condition_tokens: s.condition_tokens ?? [],
    condition: s.condition ?? null,
    severity: null,
    remarks: null,
    photo_reference: s.photo_reference ?? null,
    bbox_2d: null,
    ai_confidence: s.confidence ?? null,
    human_verified: true,
  };
}

/** Whether the damage list has the suggested part already (by part and
 * side: a "(side undetermined)" item covers both sides). */
const isListed = (s: SecondLookItem, listed: Set<string>) => (s.covers?.length ? s.covers : [s.part]).some((p) => listed.has(p));

/** The second look's suggestions on the Review step: parts around the damage
 * found that the AI saw damaged in a photo on a second, closer look. Nothing
 * is added until the surveyor ticks it and presses Add. */
export function SecondLookPanel({ data, onChange, photos }: Props) {
  const look = (data as DataWithLook).second_look ?? null;
  const [ticked, setTicked] = useState<Set<string>>(() => new Set());
  const damage = data.damage_summary;
  const shown = useMemo(() => {
    const listed = new Set((damage || []).map((d) => d.part));
    const dismissed = new Set(look?.dismissed || []);
    return (look?.items || []).filter((s) => !dismissed.has(s.part) && !isListed(s, listed));
  }, [look, damage]);

  if (!look) return null;
  if (shown.length === 0) {
    // Quiet: a line when it couldn't run on photos it had, nothing when it
    // was off, had nothing to look at, or every suggestion is dealt with.
    const say = look.note && (look.status === "failed" || (look.status === "skipped" && (look.photos_sent ?? 0) > 0));
    return say ? (
      <section className="card intake-panel">
        <span className="intake-muted">Second look: {look.note}</span>
      </section>
    ) : null;
  }

  const fromHistory = look.scope && shown.some((s) => s.source === "kit") ? ` (from ${look.scope})` : "";
  const picked = shown.filter((s) => ticked.has(s.part));

  const toggle = (part: string, on: boolean) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(part);
      else next.delete(part);
      return next;
    });

  const addTicked = () => {
    if (!picked.length) return;
    const items = picked.map(itemFromSuggestion);
    onChange((d) => ({ ...d, damage_summary: [...(d.damage_summary || []), ...items] }));
    setTicked(new Set());
  };

  const dismiss = (part: string) => {
    toggle(part, false);
    onChange((d) => {
      const current = (d as DataWithLook).second_look;
      if (!current) return d;
      const next: DataWithLook = { ...d, second_look: { ...current, dismissed: [...(current.dismissed || []), part] } };
      return next;
    });
  };

  return (
    <section className="card intake-panel">
      <div className="intake-panel-head">
        <h2>Second look</h2>
        <span className="intake-muted">Also check these{fromHistory}. Nothing is added until you tick it.</span>
      </div>
      <ul className="rv-part-groups" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {shown.map((s) => {
          const photo = photoForReference(s.photo_reference, photos);
          const name = s.name || s.part;
          return (
            <li key={s.part} className="rv-part-group">
              <div className="rv-part-group-head">
                <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={ticked.has(s.part)} onChange={(e) => toggle(s.part, e.target.checked)} aria-label={`Add ${name}`} />
                  <span className="rv-part-name">{name}</span>
                </label>
                {photo && (
                  <a className="intake-source-chip" href={fileUrl(photo.preview_url)} target="_blank" rel="noreferrer" title={`Open ${photo.label}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={fileUrl(photo.thumb_url)} alt="" />
                    {photo.label}
                  </a>
                )}
                {s.condition && <span className="intake-chip">{s.condition}</span>}
                {s.note && <span className="intake-muted">&ldquo;{s.note}&rdquo;</span>}
                {s.confidence === "Low" && (
                  <span className="intake-muted" title="The AI was unsure: check the photo">
                    (unsure)
                  </span>
                )}
                <span className="rv-part-actions">
                  <button type="button" className="intake-link" onClick={() => dismiss(s.part)} aria-label={`Dismiss ${name}`} title="Not damaged: don't suggest it again">
                    <X style={{ width: 13, height: 13, verticalAlign: "-2px" }} /> Dismiss
                  </button>
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button type="button" className="btn-secondary-modern" onClick={addTicked} disabled={!picked.length}>
          <Plus style={{ width: 14, height: 14 }} /> Add ticked parts{picked.length ? ` (${picked.length})` : ""}
        </button>
      </div>
    </section>
  );
}
