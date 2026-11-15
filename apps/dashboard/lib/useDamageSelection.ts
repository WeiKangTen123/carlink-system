import { useState } from "react";
import type { Selection } from "./findings";

/** The one selection and hover the damage map, the damage table, the
 * suggestion cards and the evidence viewer share on a page (contract9.md
 * section 7.2). Hover only outlines; the camera never moves on it. The 3D
 * damage map agent owns this file. */
export function useDamageSelection(): {
  selection: Selection;
  setSelection: (s: Selection) => void;
  hover: Selection;
  setHover: (s: Selection) => void;
} {
  const [selection, setSelection] = useState<Selection>(null);
  const [hover, setHover] = useState<Selection>(null);
  return { selection, setSelection, hover, setHover };
}
