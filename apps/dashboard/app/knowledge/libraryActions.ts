"use server";

import { unstable_rethrow } from "next/navigation";
import { API_BASE_URL, apiError, apiFetch } from "@/lib/api";
import type { Library } from "@/lib/library";

/** The parts library (GET /library, any signed-in account; counts and the
 * price book's fitting names for staff only). Loaded when the Knowledge
 * page's "Parts library" tab opens. Errors are returned so the tab can show
 * them inline. */
export async function getLibraryAction(): Promise<Library | { error: string }> {
  try {
    const res = await apiFetch(`${API_BASE_URL}/library`, { cache: "no-store" }, { timeoutMs: 15000 });
    if (!res.ok) throw await apiError(res, "Failed to load the parts library");
    const body = (await res.json()) as Library;
    return { ...body, fittings: body.fittings ?? [], twins: body.twins ?? [] };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { error: err instanceof Error ? err.message : "Failed to load the parts library" };
  }
}
