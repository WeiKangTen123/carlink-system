import { unstable_rethrow } from "next/navigation";
import { getPartsHistory, getTaxonomy, type PartsHistory } from "@/lib/api";
import { zoneKeyFor, isKnownUnmappedPart } from "@/lib/vehicleZones";
import { KnowledgeClient } from "./KnowledgeClient";

/** The imported past reports, or why they couldn't be loaded (the page
 * still shows the rest). */
async function loadPartsHistory(): Promise<{ history: PartsHistory | null; error: string | null }> {
  try {
    return { history: await getPartsHistory(), error: null };
  } catch (err) {
    // A redirect to sign-in (lib/api.ts) must reach Next, not become an error message.
    unstable_rethrow(err);
    return { history: null, error: err instanceof Error ? err.message : "Failed to load the parts price history" };
  }
}

export default async function KnowledgePage() {
  const [taxonomy, partsHistory] = await Promise.all([getTaxonomy(), loadPartsHistory()]);

  // Zone resolution lives in the dashboard (it's what drives the 3D model),
  // so it's joined here rather than asked of the API. Three states, kept
  // distinct on purpose: mapped to a mesh, deliberately declared as having
  // no mesh, or -- if this ever appears -- a part nobody has decided about,
  // which is the drift check_taxonomy_sync.py exists to catch.
  const parts = taxonomy.parts.map((part) => {
    const zone = zoneKeyFor(part);
    return {
      part,
      zone,
      status: zone ? ("mapped" as const) : isKnownUnmappedPart(part) ? ("no-mesh" as const) : ("undecided" as const),
      uses: taxonomy.usage.parts[part] ?? 0,
    };
  });

  return <KnowledgeClient taxonomy={taxonomy} parts={parts} history={partsHistory.history} historyError={partsHistory.error} />;
}
