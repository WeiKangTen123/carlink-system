"use client";

import { useEffect, useState } from "react";
import { getLibraryAction } from "@/app/knowledge/libraryActions";
import type { Library } from "./library";

/** The parts library (GET /library) for the part picker: loaded once and
 * kept for a few minutes, so opening the picker again doesn't wait. The
 * server keeps its staff extras for 5 minutes too (api/library.py). A
 * failure isn't kept: the next opening asks again, and meanwhile the
 * picker searches the part list alone. */
const KEEP_MS = 5 * 60 * 1000;
let kept: { at: number; promise: Promise<Library | { error: string }> } | null = null;

function load(): Promise<Library | { error: string }> {
  if (!kept || Date.now() - kept.at > KEEP_MS) {
    const promise = getLibraryAction().catch((err: unknown) => ({ error: err instanceof Error ? err.message : "Failed to load the parts library" }));
    kept = { at: Date.now(), promise };
    void promise.then((r) => {
      if ("error" in r && kept?.promise === promise) kept = null;
    });
  }
  return kept.promise;
}

export type PartsLibraryState = { library: Library | null; error: string | null; loading: boolean };

export function usePartsLibrary(): PartsLibraryState {
  const [state, setState] = useState<PartsLibraryState>({ library: null, error: null, loading: true });
  useEffect(() => {
    let live = true;
    void load().then((r) => {
      if (!live) return;
      setState("error" in r ? { library: null, error: r.error, loading: false } : { library: r, error: null, loading: false });
    });
    return () => {
      live = false;
    };
  }, []);
  return state;
}
