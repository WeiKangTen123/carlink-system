import { useCallback, useEffect, useRef, useState } from "react";
import { decideReportSuggestionAction } from "@/app/reports/actions";
import type { CaseDecideResult, DecideAction, DecideResult } from "./api";
import { IntakeRequestError, decideIntakeSuggestion } from "./intake";

/** Plain words for the decide endpoints' refusals (contract9.md section 4.4). */
const DECIDE_MESSAGES: Record<string, string> = {
  running: "The AI is reading the case right now. Decide once it has finished.",
  signed_off: "The case is signed off, so its suggestions can't be changed. Reopen it first.",
  unknown_suggestion: "That suggestion isn't there any more (the AI may have read the case again). Reload the page.",
  bad_action: "That decision couldn't be saved. Reload the page and try again.",
};

/** The words for a failed decision: the plain words for its code, else
 * the API's own. */
function decideMessage(code: string | null | undefined, said: string | null | undefined): string {
  return (code && DECIDE_MESSAGES[code]) || said || "Couldn't save the decision. Try again.";
}

/** The suggestion's own key (prefill.item_key) from a finding key
 * ("sug:<key>", lib/findings.ts) or the key itself. Every component passes
 * finding keys; the endpoints take the suggestion's. */
export const suggestionKey = (key: string) => (key.startsWith("sug:") ? key.slice(4) : key);

/** Add / Not damaged / undo on a suggestion through the server's decide
 * endpoint (POST /intake/{id}/suggestions on the wizard, POST
 * /reports/{id}/suggestions on the case page; contract9.md sections 4.4 and
 * 7.2). The server stamps who and when, and owns second_look: a whole-draft
 * save never changes it, so a decision is never lost to autosave.
 *
 * - `key` may be the finding key ("sug:<key>") or the suggestion's own.
 * - The wizard puts the answer into its local draft (applyToDraft). It is
 *   also told what was asked, so an undone add comes off its list too
 *   (lib/findings.ts applyDecision).
 * - The case page takes the saved answer (onSaved), which also carries the
 *   parts and the re-synced Annex A (CaseDecideResult).
 *
 * Decisions go one at a time, in the order made, so "Add" then a quick
 * "Undo" reach the server in that order. decide() rejects when the server
 * refuses (and `error` says why), so the caller can stay where it is. */
export function useSuggestionDecisions(
  o:
    | { surface: "intake"; reportId: string; applyToDraft: (r: DecideResult, asked?: { key: string; action: DecideAction }) => void }
    | { surface: "case"; reportId: string; onSaved: (r: CaseDecideResult) => void },
): {
  decide: (key: string, action: DecideAction, looked: boolean) => Promise<void>;
  busyKey: string | null;
  error: string | null;
} {
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The latest options, so decide() keeps one identity across renders (the
  // 3D map and the cards hold on to it).
  const latest = useRef(o);
  useEffect(() => {
    latest.current = o;
  });
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const decide = useCallback((key: string, action: DecideAction, looked: boolean): Promise<void> => {
    const run = async () => {
      const opts = latest.current;
      const sKey = suggestionKey(key);
      setBusyKey(sKey);
      setError(null);
      try {
        const body = { key: sKey, action, looked: Boolean(looked) };
        if (opts.surface === "intake") {
          let r: DecideResult;
          try {
            r = await decideIntakeSuggestion(opts.reportId, body);
          } catch (e) {
            throw new Error(e instanceof IntakeRequestError ? decideMessage(e.code, e.message) : decideMessage(null, null));
          }
          opts.applyToDraft(r, { key: sKey, action });
        } else {
          const r = await decideReportSuggestionAction(opts.reportId, body);
          if ("error" in r) throw new Error(decideMessage(r.code, r.error));
          opts.onSaved(r);
        }
      } catch (e) {
        const message = e instanceof Error ? e.message : decideMessage(null, null);
        setError(message);
        throw new Error(message);
      } finally {
        setBusyKey(null);
      }
    };
    const next = queue.current.then(run, run);
    // The queue itself never rejects; each caller gets its own outcome.
    queue.current = next.catch(() => undefined);
    return next;
  }, []);

  return { decide, busyKey, error };
}
