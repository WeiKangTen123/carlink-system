import type { DecideAction, DecideResult } from "./api";

/** Add / Not damaged / undo on a suggestion through the server's decide
 * endpoint (POST /intake/{id}/suggestions on the wizard, POST
 * /reports/{id}/suggestions on the case page; contract9.md sections 4.4 and
 * 7.2). The wizard puts the returned item into its local draft
 * (applyToDraft); the case page takes the saved answer (onSaved).
 *
 * Step 1 stub: decide does nothing yet -- the evidence UI agent wires it
 * to the endpoint the decisions agent builds. */
export function useSuggestionDecisions(
  o:
    | { surface: "intake"; reportId: string; applyToDraft: (r: DecideResult) => void }
    | { surface: "case"; reportId: string; onSaved: (r: DecideResult) => void },
): {
  decide: (key: string, action: DecideAction, looked: boolean) => Promise<void>;
  busyKey: string | null;
  error: string | null;
} {
  void o;
  return { decide: async () => {}, busyKey: null, error: null };
}
