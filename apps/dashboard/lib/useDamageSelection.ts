import { useCallback, useState } from "react";
import type { Selection } from "./findings";
import { sameSelection } from "./damageLinks";

/** The one selection and hover the damage map, the damage table, the
 * suggestion cards and the evidence viewer share on a page (contract9.md
 * section 7.2). Hover only outlines; the camera never moves on it. The 3D
 * damage map agent owns this file.
 *
 * setSelection always takes the new value, even an equal one: a second
 * [Map] / [Show on 3D] on the same part is a new request, and the map flies
 * the camera back to it. setHover ignores a value equal to the current one,
 * so a pointer moving over one part re-renders nothing. */
export function useDamageSelection(): {
  selection: Selection;
  setSelection: (s: Selection) => void;
  hover: Selection;
  setHover: (s: Selection) => void;
} {
  const [selection, setSelection] = useState<Selection>(null);
  const [hover, setHoverState] = useState<Selection>(null);
  const setHover = useCallback((s: Selection) => setHoverState((prev) => (sameSelection(prev, s) ? prev : s)), []);
  return { selection, setSelection, hover, setHover };
}
