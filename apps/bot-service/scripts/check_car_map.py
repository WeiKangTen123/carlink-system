"""Checks a car map (app/intake/car_map.json by default) and the proposed new
part names (app/intake/car_map_new_names.json). Format and rules:
plans/2026-11-18-car-knowledge/K1-design.md; the check itself is
app/intake/car_map.py validate().

    python scripts/check_car_map.py                 # the map, the new names
    python scripts/check_car_map.py path/to/map.json
    python scripts/check_car_map.py --l1            # L1 as a car map instead

Exit 1 on any problem. A problem is what validate() says:
  - every name, {side} expanded, is a canonical part (never "(side
    undetermined)", never Other) or a new name, and "new" says which;
  - ids once each (assemblies; relations and chains share one set; signs);
  - a relation's side matches where {side} is (symmetric expansion), its
    parent is a member of its own assembly and its child of some assembly;
  - signs point at relations and unlock chains that exist, from an anchor
    of their own assembly; every chain is unlocked by some sign;
  - a chain is two relations end to start: at most 2 hops, no cycle;
  - enums, flags, priors 1-5 with a source, reasons <= 160 characters with
    no 3+ digit number, no practice make or model anywhere.
Notes (never a failure) help the builder (K1b): assemblies with no signs,
relations with no prior, parents with more relations than the cap of 12,
new names used, the compiled table's size.

It also prints the map's sha256 as git keeps it (LF), for the freeze line.
"""
import argparse
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # apps/bot-service
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="check-car-map-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("SECRETS_DIR", str(Path(_tmp, "secrets")))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'check.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake-check-car-map")
os.environ.setdefault("RUN_BACKGROUND_CLEANUP", "false")

from app.intake import car_map, links  # noqa: E402

CAP = 12  # intake_links_max's default: more relations on one parent than this always hit it


def notes_for(cm: dict) -> list[str]:
    out = []
    for asm in cm.get("assemblies") or []:
        if asm.get("anchors") and not asm.get("signs"):
            out.append(f"assembly {asm['id']!r}: anchors but no signs (no walk, no second hop)")
    unranked = [r["id"] for r in cm.get("relations") or [] if not r.get("prior")]
    if unranked:
        out.append(f"{len(unranked)} relation(s) with no prior (ordered by table order inside the cap)")
    per_parent = Counter(r["parent"] for r in cm.get("relations") or [])
    for parent, n in per_parent.most_common():
        if n > CAP:
            out.append(f"parent {parent!r}: {n} relations, more than the cap of {CAP}")
    new = sorted({m["part"] for a in cm.get("assemblies") or [] for m in a.get("members") or []
                  if any(full not in links.CANONICAL_PARTS for full in car_map.expand(m["part"]))})
    if new:
        out.append(f"{len(new)} new name(s) used (taxonomy.CAR_MAP_PARTS; left out of the compiled table while intake_car_map_names is off): {', '.join(new)}")
    return out


def summary(cm: dict) -> str:
    rels = cm.get("relations") or []
    types = Counter(r.get("type") for r in rels)
    signs = sum(len(a.get("signs") or []) for a in cm.get("assemblies") or [])
    compiled = car_map.links_table(cm)
    return (f"version {cm.get('version')}: {len(cm.get('assemblies') or [])} assemblies, {len(rels)} relations "
            f"({', '.join(f'{k} {v}' for k, v in sorted(types.items()))}), {len(cm.get('chains') or [])} chains, "
            f"{signs} signs; compiled table {len(compiled['links'])} links")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", help="the car map to check (default app/intake/car_map.json)")
    ap.add_argument("--l1", action="store_true", help="check L1 (link_table.json) converted by from_l1 instead")
    args = ap.parse_args(argv)
    problems: list[str] = []

    doc = car_map.load_new_names()
    names_errors = car_map.validate_new_names(doc) if doc is not None else ["the new names file didn't load (see the log)"]
    problems += [f"new names: {e}" for e in names_errors]
    if doc is not None:
        print(f"new names {doc.get('version')}: {len(doc['names'])} entries, "
              f"{len(car_map.new_name_set(doc))} names with sides expanded")

    if args.l1:
        if links.TABLE is None:
            problems.append("L1 (link_table.json) didn't load")
            cm = None
        else:
            cm = car_map.from_l1(links.TABLE)
            if car_map.links_table(cm) != links.TABLE:
                problems.append("from_l1 -> links_table is not L1 again")
        label = "L1 as a car map"
    else:
        path = Path(args.path) if args.path else car_map.MAP_PATH
        label = str(path)
        if not path.exists():
            print(f"{label}: no car map yet (K1b writes it); nothing more to check")
            cm = None
        else:
            import json

            try:
                cm = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                problems.append(f"{label}: can't be read ({type(exc).__name__})")
                cm = None
            if cm is not None:
                print(f"{label}: sha256 (LF) {car_map.sha256_lf(path)}")
    if cm is not None:
        errors = car_map.validate(cm)
        problems += [f"{label}: {e}" for e in errors]
        if not errors:
            print(summary(cm))
            table_errors = links.validate_table(car_map.links_table(cm))
            problems += [f"{label}: compiled table: {e}" for e in table_errors]
            for note in notes_for(cm):
                print(f"  note: {note}")

    for line in problems:
        print(f"PROBLEM {line}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
