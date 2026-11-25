"use client";

import { useEffect, useState } from "react";
import { getLibraryAction } from "@/app/knowledge/libraryActions";
import type { Library } from "./library";

/** The parts library (GET /library) for the part picker: loaded once and
 * kept for a few minutes, so opening the picker again doesn't wait. The
 * server keeps its staff extras for 5 minutes too (api/library.py). A
 * failure isn't kept: the next opening asks again, and meanwhile the
 * picker searches the part list alone (zones from lib/vehicleZones).
 *
 * The pages with a picker prefetch it (prefetchPartsLibrary) when they
 * open, and a picker opened after it arrived starts from it at once: no
 * list-only first draw swapped for the library's (review M2, L7). The
 * copy lives in the tab: signing out or in reloads the page. */
const KEEP_MS = 5 * 60 * 1000;
let kept: { at: number; promise: Promise<Library | { error: string }>; value: Library | null } | null = null;

function load(): Promise<Library | { error: string }> {
  if (!kept || Date.now() - kept.at > KEEP_MS) {
    const promise = getLibraryAction().catch((err: unknown) => ({ error: err instanceof Error ? err.message : "Failed to load the parts library" }));
    const entry: NonNullable<typeof kept> = { at: Date.now(), promise, value: kept?.value ?? null };
    kept = entry;
    void promise.then((r) => {
      if (kept !== entry) return;
      if ("error" in r) kept = null;
      else entry.value = r;
    });
  }
  return kept.promise;
}

/** Starts loading the library (or refreshes a stale copy) without waiting. */
export function prefetchPartsLibrary(): void {
  void load();
}

export type PartsLibraryState = { library: Library | null; error: string | null; loading: boolean };

export function usePartsLibrary(): PartsLibraryState {
  const [state, setState] = useState<PartsLibraryState>(() =>
    kept?.value ? { library: kept.value, error: null, loading: false } : { library: null, error: null, loading: true }
  );
  useEffect(() => {
    let live = true;
    void load().then((r) => {
      if (!live) return;
      // A failed refresh of a copy still in use says nothing: the picker
      // searches that copy, not the bare part list (review N11).
      setState((cur) =>
        "error" in r
          ? { library: cur.library, error: cur.library ? null : r.error, loading: false }
          : cur.library === r
            ? cur
            : { library: r, error: null, loading: false }
      );
    });
    return () => {
      live = false;
    };
  }, []);
  return state;
}
