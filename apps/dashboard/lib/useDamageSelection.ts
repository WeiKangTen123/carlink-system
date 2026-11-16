import { useCallback, useState } from "react";
import type { Selection } from "./findings";
import { afterItemsRemoved, sameSelection } from "./damageLinks";

/** The one selection and hover the damage map, the damage table, the
 * suggestion cards and the evidence viewer share on a page (contract9.md
 * section 7.2). Hover only outlines; the camera never moves on it. The 3D
 * damage map agent owns this file.
 *
 * setSelection always takes the new value, even an equal one: a second
 * [Map] / [Show on 3D] on the same part is a new request, and the map flies
 * the camera back to it. setHover ignores a value equal to the current one,
 * so a pointer moving over one part re-renders nothing.
 *
 * itemsRemoved: damage-list rows went from under the selection (an undone
 * add took its item off): an "item:<n>" selection or hover follows its row
 * up, or is dropped with it (lib/damageLinks afterItemsRemoved). It works on
 * the values as they are when it runs, so a decision's late answer can call
 * it. */
export function useDamageSelection(): {
  selection: Selection;
  setSelection: (s: Selection) => void;
  hover: Selection;
  setHover: (s: Selection) => void;
  itemsRemoved: (indices: number[]) => void;
} {
  const [selection, setSelection] = useState<Selection>(null);
  const [hover, setHoverState] = useState<Selection>(null);
  const setHover = useCallback((s: Selection) => setHoverState((prev) => (sameSelection(prev, s) ? prev : s)), []);
  const itemsRemoved = useCallback((indices: number[]) => {
    if (!indices.length) return;
    setSelection((prev) => afterItemsRemoved(prev, indices));
    setHoverState((prev) => afterItemsRemoved(prev, indices));
  }, []);
  return { selection, setSelection, hover, setHover, itemsRemoved };
}
