"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { getIntake, type IntakeState } from "@/lib/intake";

/** GET /intake/{id}'s read_by (ai/runs.read_by on the API): the newest
 * reading was answered by a backup model of the chain because the main
 * model's free daily allowance was used up. Null otherwise, and absent from
 * an older API. */
export type ReadBy = {
  model: string;
  main_model: string;
  /** When the main model's allowance comes back (midnight US Pacific), ISO. */
  main_spent_until: string;
};

function readByOf(state: IntakeState): ReadBy | null {
  const value = (state as IntakeState & { read_by?: unknown }).read_by;
  if (!value || typeof value !== "object") return null;
  const r = value as Partial<ReadBy>;
  return typeof r.model === "string" && typeof r.main_model === "string" && typeof r.main_spent_until === "string"
    ? { model: r.model, main_model: r.main_model, main_spent_until: r.main_spent_until }
    : null;
}

/** "4:00 pm" in the viewer's own time, "4:00 pm tomorrow" when that is
 * their next day; null once it has passed (or can't be read). */
function untilWords(iso: string, now: number): string | null {
  const until = new Date(iso);
  if (Number.isNaN(until.getTime()) || until.getTime() <= now) return null;
  const time = until.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const today = new Date(now);
  if (until.toDateString() === today.toDateString()) return time;
  const tomorrow = new Date(now);
  tomorrow.setDate(today.getDate() + 1);
  if (until.toDateString() === tomorrow.toDateString()) return `${time} tomorrow`;
  return `${until.toLocaleDateString(undefined, { weekday: "long" })} ${time}`;
}

/** The review step's "(i) Read by the backup model (gemini-3.6-flash):
 * today's main-model allowance is used up until 4:00 pm." Everyone's AI
 * runs on free Gemini keys, and the main model allows only a few reads a
 * day on each; a backup model's reading is thinner, so the surveyor is
 * told rather than left to wonder. Nothing at all when the main model read
 * the case.
 *
 * Self-contained, so the review step mounts it with one line: it asks the
 * API for the draft itself, by the id the wizard keeps in the URL
 * (/reports/new?draft=<id>), and asks again when a new reading changes
 * which photos were read (readPhotoIds). */
export function ReadByNotice({ readPhotoIds }: { readPhotoIds: string[] }) {
  const [found, setFound] = useState<{ readBy: ReadBy; at: number } | null>(null);
  const readKey = readPhotoIds.join(",");

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("draft");
    if (!id) return;
    let live = true;
    getIntake(id)
      .then((state) => {
        if (!live) return;
        const readBy = readByOf(state);
        setFound(readBy ? { readBy, at: Date.now() } : null);
      })
      .catch(() => {
        // Only a notice: without the answer it stays as it was.
      });
    return () => {
      live = false;
    };
  }, [readKey]);

  if (!found) return null;
  const { readBy, at } = found;
  const until = untilWords(readBy.main_spent_until, at);
  return (
    <div className="ai-off-hint" role="status" style={{ marginTop: 0, fontSize: 12 }} title={`Main model: ${readBy.main_model}`}>
      <Info style={{ width: 14, height: 14, flexShrink: 0, marginTop: 1 }} />
      <span>
        Read by the backup model ({readBy.model}):{" "}
        {until
          ? `today's main-model allowance is used up until ${until}.`
          : "the main model's daily allowance was used up when this case was read."}
      </span>
    </div>
  );
}
