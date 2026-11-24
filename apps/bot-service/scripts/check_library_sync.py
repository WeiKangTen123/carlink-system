"""Checks that the committed parts library core (app/parts/library_core.json)
is exactly what today's code makes of it (app/parts/library.py build_core:
the taxonomy, the firm's wording, the car map, the link table, the price
book's families and the dashboard's 3D zone table). The core is code: a
part, a wording or a zone changed in one of those places without this file
following fails CI, the same way check_taxonomy_sync.py keeps the zone
table in step.

    python scripts/check_library_sync.py           # check
    python scripts/check_library_sync.py --write   # regenerate the file

Offline and read-only (without --write): no database, no AI.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.parts import library  # noqa: E402


def _differences(old: dict, new: dict) -> list[str]:
    """A few readable lines on what changed, for the failure message."""
    out = []
    for section in ("areas", "assemblies", "components", "twins", "families"):
        a = {e.get("key") or e.get("name"): e for e in old.get(section) or []}
        b = {e.get("key") or e.get("name"): e for e in new.get(section) or []}
        for key in b.keys() - a.keys():
            out.append(f"  {section}: new {key!r}")
        for key in a.keys() - b.keys():
            out.append(f"  {section}: gone {key!r}")
        for key in a.keys() & b.keys():
            if a[key] != b[key]:
                fields = sorted(f for f in set(a[key]) | set(b[key]) if a[key].get(f) != b[key].get(f))
                out.append(f"  {section}: {key!r} differs in {', '.join(fields)}")
    for key in ("format", "version", "built_from", "other"):
        if old.get(key) != new.get(key):
            out.append(f"  {key} differs")
    return out[:40]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="write the core the code gives to library_core.json")
    args = parser.parse_args()

    if not library.ZONES_TS.exists():
        print(f"Cannot find {library.ZONES_TS} (the dashboard's zone table)")
        return 1
    core = library.build_core()
    text = library.dumps(core)
    if args.write:
        library.CORE_PATH.write_text(text, encoding="utf-8", newline="\n")
        print(f"  wrote {library.CORE_PATH.name}: {len(core['components'])} components, {len(core['twins'])} twins, "
              f"{core['core']}")
        return 0
    if not library.CORE_PATH.exists():
        print(f"  {library.CORE_PATH.name} is missing: run python scripts/check_library_sync.py --write")
        return 1
    # A Windows checkout may hold the file with CRLF line ends.
    committed = library.CORE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    if committed != text:
        import json

        try:
            old = json.loads(committed)
        except ValueError:
            old = {}
        print(f"  {library.CORE_PATH.name} is not what the code gives (committed {old.get('core')}, "
              f"code {core['core']}):")
        for line in _differences(old, core) or ["  (formatting only)"]:
            print(line)
        print("\n  Run python scripts/check_library_sync.py --write and commit the file.")
        return 1
    print(f"  library core in sync: {len(core['components'])} components, {len(core['twins'])} twins, "
          f"{len(core['assemblies'])} assemblies, {len(core['areas'])} areas ({core['core']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
