"""Verifies every canonical part has an explicit decision in the dashboard.

The taxonomy (Python) and the zone map (TypeScript) live in separate
packages, so they can drift. A part present here but missing there renders
without a 3D marker and without any explanation -- exactly the silent gap
this taxonomy was introduced to remove. Run after editing either list.

    python3 scripts/check_taxonomy_sync.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.reports.taxonomy import CANONICAL_PARTS  # noqa: E402

TS = Path(__file__).resolve().parents[3] / "apps/dashboard/lib/vehicleZones.ts"


def main() -> int:
    if not TS.exists():
        print(f"Cannot find {TS}")
        return 1
    src = TS.read_text()
    block = re.search(r"CANONICAL_ZONES:\s*Record<string,\s*string \| null>\s*=\s*\{(.*?)\n\};", src, re.S)
    if not block:
        print("Could not locate CANONICAL_ZONES in vehicleZones.ts")
        return 1

    mapped = set(re.findall(r'"([^"]+)":\s*(?:"[a-z_]+"|null)', block.group(1)))
    missing = [p for p in CANONICAL_PARTS if p not in mapped]
    extra = [p for p in mapped if p not in CANONICAL_PARTS]

    for p in missing:
        print(f"  MISSING from vehicleZones.ts: {p!r}")
    for p in extra:
        print(f"  EXTRA in vehicleZones.ts (not a canonical part): {p!r}")

    if missing or extra:
        print(f"\n{len(missing)} missing, {len(extra)} extra -- taxonomy and zone map are out of sync")
        return 1
    print(f"  all {len(CANONICAL_PARTS)} canonical parts have an explicit zone decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
