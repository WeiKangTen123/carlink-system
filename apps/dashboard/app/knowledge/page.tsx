import { getTaxonomy } from "@/lib/api";
import { zoneKeyFor, isKnownUnmappedPart } from "@/lib/vehicleZones";
import { KnowledgeClient } from "./KnowledgeClient";

export default async function KnowledgePage() {
  const taxonomy = await getTaxonomy();

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

  return <KnowledgeClient taxonomy={taxonomy} parts={parts} />;
}
