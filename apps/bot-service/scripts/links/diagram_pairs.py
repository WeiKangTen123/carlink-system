"""Which of OUR parts the maker's catalogue draws together (contract 10, linked parts).

    python scripts/links/diagram_pairs.py CATALOGUE_DB [--out report.md]

Reads the worker's parsed catalogue (the sqlite file the parts worker keeps,
catalogue.db) READ-ONLY, maps every row of every cached diagram to one of our
canonical parts with app.parts.oem_names (the part without its side: "Tail
Lamp", "Front Door"), and writes Markdown:

  (a) per diagram: its name -> the parts of ours it holds;
  (b) pairs of our parts, and how close the catalogue puts them:
        same diagram    both on one diagram (the strongest sign)
        next diagram    on neighbouring diagrams of one group, in the order
                        the site lists them (both cached)
        same group      only the same group of the catalogue ("BODY PARTS")
      with the car families that show it.

Pairs never join a front part to a rear one: some diagrams hold both ends
(a Civic's bumpers) and the row's own FR./RR. decides, not the diagram.

Generic co-membership only: which of our parts share a diagram. No case, plate
or claim is read; the input is the catalogue's own rows.

Limits are printed at the top of the report: only diagrams the worker has
already fetched are in the cache (a car lists far more than it holds rows
for), and the pairs are as good as the row-name mapper (None is left out).
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from app.parts.oem_names import end_of, to_family  # noqa: E402

# Which cars are one family of ours: the cache holds three catalogue cars of
# the HR-V (different years and engines), and they are one car to a surveyor.
FAMILIES = {("HONDA", "CIVIC"): "Honda Civic", ("HONDA", "HRV"): "Honda HR-V", ("BMW", "X1"): "BMW X1"}
TIERS = ("same diagram", "next diagram", "same group")


def load(db_path: str) -> dict:
    """{car family: {"rows": n, "mapped": n, "diagrams": [diagram dicts]}} from a read-only open."""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        cars = conn.execute("select id, brand, model from vehicles where source = 'microfiches'").fetchall()
        out: dict[str, dict] = {}
        for vehicle_id, brand, model in cars:
            family = FAMILIES.get(((brand or "").upper(), (model or "").upper().replace("-", "")))
            if family is None:
                continue
            groups = {g: n for g, n in conn.execute(
                "select id, name from groups where source = 'microfiches' and vehicle_id = ?", (vehicle_id,))}
            listing = conn.execute(
                "select id, group_id, name from diagrams where source = 'microfiches' and vehicle_id = ? order by pk",
                (vehicle_id,)).fetchall()
            position: dict[str, int] = defaultdict(int)  # a diagram's place in its group's listing
            listed = []
            for diagram_id, group_id, name in listing:
                listed.append((diagram_id, groups.get(group_id, group_id), name, position[group_id]))
                position[group_id] += 1
            entry = out.setdefault(family, {"rows": 0, "mapped": 0, "diagrams": []})
            for diagram_id, group, name, place in listed:
                names = [n for (n,) in conn.execute(
                    "select name from part_rows where source = 'microfiches' and vehicle_id = ? and diagram_id = ? "
                    "order by row_index", (vehicle_id, diagram_id))]
                if not names:
                    continue  # listed by the site, not fetched yet
                parts: list[str] = []
                for row_name in names:
                    entry["rows"] += 1
                    found = to_family(row_name, name)
                    if found:
                        entry["mapped"] += 1
                        if found[0] not in parts:
                            parts.append(found[0])
                entry["diagrams"].append({"group": group, "name": name, "place": place, "parts": parts,
                                          "rows": len(names), "vehicle": vehicle_id})
        return out
    finally:
        conn.close()


def merge_same_diagrams(diagrams: list[dict]) -> list[dict]:
    """A car family made of several catalogue cars lists the same diagram more
    than once ("TAILLIGHT" on two HR-V cars): one diagram, its parts united."""
    merged: dict[tuple, dict] = {}
    for d in diagrams:
        found = merged.setdefault((d["group"], d["name"]), {**d, "parts": [], "rows": 0})
        found["rows"] += d["rows"]
        found["place"] = min(found["place"], d["place"])
        found["parts"] += [p for p in d["parts"] if p not in found["parts"]]
    return list(merged.values())


def compatible(a: str, b: str) -> bool:
    ends = end_of(a), end_of(b)
    return not (ends[0] and ends[1] and ends[0] != ends[1])


def pair_tiers(diagrams: list[dict]) -> dict[tuple[str, str], str]:
    """(part A, part B) -> the closest tier the catalogue puts them in, for one car family."""
    best: dict[tuple[str, str], int] = {}

    def note(a: str, b: str, tier: int) -> None:
        if a != b and compatible(a, b):
            key = tuple(sorted((a, b)))
            best[key] = min(best.get(key, 9), tier)

    for d in diagrams:
        for a, b in combinations(d["parts"], 2):
            note(a, b, 0)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for d in diagrams:
        by_group[d["group"]].append(d)
    for group in by_group.values():
        group.sort(key=lambda d: d["place"])
        for i, d in enumerate(group):
            for a in d["parts"]:
                for e in group[i + 1:]:
                    nearest = e is group[i + 1] and e["place"] - d["place"] == 1
                    for b in e["parts"]:
                        note(a, b, 1 if nearest else 2)
    return {key: TIERS[tier] for key, tier in best.items()}


def report(data: dict[str, dict]) -> str:
    lines = [
        "# Diagram pairs: which of our parts the catalogue draws together",
        "",
        "Generic co-membership from the maker's microfiche diagrams (parts-honda.uk, bmw-spare-parts.com), "
        "read from the worker's cache. Parts are ours without a side (\"Tail Lamp\" is Left and Right).",
        "",
        "## Coverage limits",
        "",
        "- Only diagrams the worker has already fetched are here. A catalogue car lists 100-150 diagrams and the "
        "cache holds rows for a few of them, so a missing pair means \"not looked at\", never \"not linked\".",
    ]
    for family, entry in data.items():
        entry["diagrams"] = merge_same_diagrams(entry["diagrams"])
        lines.append(f"- {family}: {len(entry['diagrams'])} cached diagram(s), {entry['rows']} rows, "
                     f"{entry['mapped']} of them mapped to a part of ours ({entry['mapped'] / max(1, entry['rows']):.0%}); "
                     "the rest are fasteners, clips, labels, bulbs and trim with no part of ours.")
    lines += [
        "- The BMW X1 has one cached diagram (rear trim), so it shows little; treat the Honda cars as the evidence.",
        "- \"Next diagram\" is the order the site lists a group in, between cached diagrams only; "
        "\"same group\" is the weakest sign (a group like BODY PARTS holds a whole car's panels).",
        "- A front part is never paired with a rear one, even on a diagram that holds both ends.",
        "",
        "## (a) Per diagram",
        "",
    ]
    for family, entry in data.items():
        lines += [f"### {family}", ""]
        for d in sorted(entry["diagrams"], key=lambda d: (d["group"], d["place"])):
            shown = ", ".join(d["parts"]) if d["parts"] else "(no part of ours)"
            lines.append(f"- {d['name']} [{d['group']}]: {shown}")
        lines.append("")

    tiers = {family: pair_tiers(entry["diagrams"]) for family, entry in data.items()}
    pairs: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for family, found in tiers.items():
        for key, tier in found.items():
            pairs[key][family] = tier
    rank = {tier: i for i, tier in enumerate(TIERS)}
    ordered = sorted(pairs.items(), key=lambda kv: (min(rank[t] for t in kv[1].values()), -len(kv[1]), kv[0]))
    lines += ["## (b) Pairs", "", "| Part A | Part B | Closest | Cars showing it |", "|---|---|---|---|"]
    weak = 0
    for (a, b), shown in ordered:
        closest = min(shown.values(), key=rank.__getitem__)
        if closest == "same group":
            weak += 1  # a whole group of panels says little: counted, not listed
            continue
        cars = ", ".join(f"{family} ({tier})" for family, tier in shown.items())
        lines.append(f"| {a} | {b} | {closest} | {cars} |")
    on_one = sum(1 for _, s in ordered if "same diagram" in s.values())
    lines += ["", f"{on_one} pairs share a diagram, {len(ordered) - on_one - weak} more sit on neighbouring diagrams, "
              f"and {weak} only share a group (not listed).", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("catalogue_db", help="the worker's parsed catalogue (catalogue.db); opened read-only")
    parser.add_argument("--out", help="write the Markdown here instead of printing it")
    args = parser.parse_args()
    text = report(load(args.catalogue_db))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
