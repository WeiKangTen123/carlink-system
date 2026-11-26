"""The parts library, core and read-only (plans/2026-11-23-report-and-parts/
L-library-cycle.md section A, step 2A).

One place to look a part up: car area -> assembly -> component -> fittings.
The core is generated FROM today's code and never typed by hand:

  - the components: taxonomy.KNOWN_PARTS without the side ("Left Tail Lamp"
    and "Right Tail Lamp" are one component, "Tail Lamp", side rule
    "sided"), 86 of them; the "(side undetermined)" names are their twins,
    25 of them, each covering the components it stands for (a "Door (side
    undetermined)" is a front or a rear door). "Other / Not Listed" is the
    escape hatch, not a component;
  - the firm's wording, sedan and tail-gate (taxonomy.FIRM_PART_NAMES*);
  - the side rules (taxonomy split_side / unsided_part, _NEEDS_END,
    _NOT_PLACED);
  - the 3D zones and stand-ins, read from the dashboard's
    lib/vehicleZones.ts (the same tables the 3D map draws with);
  - the car map's assemblies (intake/car_map.json, K1.0) and the new names'
    records (car_map_new_names.json): home assembly, role, hidden,
    strip-down, body gate, the "why" note and the "also check" words;
  - the linked parts in and out (intake/link_table.json, L1);
  - the price book's family names (parts/history.py family rules).

build_core() makes it; library_core.json is it, committed, and
scripts/check_library_sync.py fails CI when the two differ, so the core is
always exactly the code. The API (app/api/library.py) serves the committed
file: the bot-service image has no dashboard folder to read zones from.

Nothing here changes what the AI sees: no prompt, schema or list reads
this module, and nothing that existed before it calls it yet (step 2A).

Fittings in 2A are "seed" rows made at request time from the price book's
own wordings (seed_fittings): names only -- no prices, no plates, no case
references, no make or model -- placed under their price-book family's
component. They are never stored and never count as evidence for anything.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.reports.taxonomy import (
    CANONICAL_PARTS,
    CAR_MAP_PARTS,
    FIRM_PART_NAMES,
    FIRM_PART_NAMES_HATCH,
    KNOWN_PARTS,
    OTHER_PART,
    SIDE_CODES,
    UNDETERMINED_SUFFIX,
    body_class,
    split_side,
    unsided_part,
)

HERE = Path(__file__).resolve().parent
CORE_PATH = HERE / "library_core.json"


def _find_repo(start: Path) -> Path | None:
    """The repository root (the folder holding apps/dashboard) above `start`:
    found on a developer's machine and in CI. None inside the api container,
    which holds only bot-service's app/ (at /app/app) -- there the committed
    library_core.json is all the library needs (load_core); only a rebuild
    (build_core, scripts/check_library_sync.py) reads the dashboard's zone
    table. (staging, 25 Nov 2026: a fixed parents[3] crashed the api there.)"""
    for folder in (start, *start.parents):
        if (folder / "apps" / "dashboard" / "lib" / "vehicleZones.ts").is_file():
            return folder
    return None


REPO = _find_repo(HERE)
ZONES_TS = REPO / "apps" / "dashboard" / "lib" / "vehicleZones.ts" if REPO else None

FORMAT = 1
# The library's version: one step per admin decision (L-library-cycle D.3).
# 2A has no decisions yet, so the library is the core alone: version 0.
LIBRARY_VERSION = 0

# Areas in plain words (A.3), in the order the page lists them.
AREAS: tuple[tuple[str, str], ...] = (
    ("front", "Front"),
    ("rear", "Rear"),
    ("side", "Side"),
    ("roof", "Roof"),
    ("under", "Under"),
    ("wheels", "Wheels"),
    ("inside", "Inside"),
)
AREA_KEYS = tuple(key for key, _ in AREAS)
# car_map.AREAS -> ours. The car map walks the roof and the restraints from
# the side; for finding a part, they are their own areas.
_CAR_MAP_AREA = {"front": "front", "rear": "rear", "side": "side", "underbody": "under", "wheel": "wheels"}
_ASSEMBLY_AREA = {"roof": "roof", "restraints": "inside"}

# A component's home assembly: the one it anchors, else the one the car
# map's new names give it, else the first that lists it -- except these,
# checked by hand once (A.3): where the first listing is a neighbour's
# assembly rather than the part's own.
_HOME = {
    "Front Wheel Arch Liner": "front-fender",
    # The rear arch is the quarter panel's (its 3D zone, and the rear arch
    # trim's home), though the firm words it "Rear bumper wheel arch
    # protector".
    "Rear Wheel Arch Liner": "rear-quarter",
    "Radiator": "front-end",
    "Air Conditioning Condenser": "front-end",
    "Steering Assembly": "front-subframe",
    "Exhaust System": "underbody",
    "Fuel Tank": "underbody",
    "Boot Side Trim": "boot-interior",
    "Interior Trim": "restraints",
    "Dashboard": "restraints",
}

KINDS = ("panel", "part", "mechanical", "electrical", "interior", "fitting")
# car_map.ROLES -> kind; every other role is a "part".
_ROLE_KIND = {"panel": "panel", "mechanical": "mechanical", "electrical": "electrical", "safety": "electrical"}

# The words that make a price-book line a small fitting (B.4 rule 4).
FITTING_WORDS = frozenset({
    "clip", "seal", "sealant", "sealer", "retainer", "sticker", "gasket", "bush", "bushing", "nut", "stopper",
    "insulator", "sponge", "cloth", "pad", "emblem", "badge",
})

# Other names the code already knows a part by, with where each comes
# from: the price book's family rules (parts/history.py _FAMILY_RULES and
# normalise_item), the 3D map's free-text rules (vehicleZones.ts
# CATEGORY_RULES), the firm's own typos. Keyed by component name.
# scripts/test_library.py checks that every alias the family rules read
# goes to its own component's family, never another's.
#
# The kind says what the other name is (step 3A, review N1):
#   - "synonym": the same thing in other words ("Hood" is the bonnet);
#   - "typo": the firm's own misspelling of it;
#   - FILED_UNDER: a sub-part or a neighbour the price book files under
#     this part ("Rear air vent" under the rear bumper, "Shock absorber"
#     under the suspension). Search still finds the part by it, but a
#     person typing it is never silently given the parent: the part picker
#     offers it as "filed under" and asks first, and a price-book wording
#     equal to it stays a wording of its own (_known_keys).
FILED_UNDER = "filed_under"
_ALIASES: dict[str, list[tuple[str, str]]] = {
    "Bonnet": [("Hood", "synonym")],
    "Front Grille": [("Grill", "synonym")],
    "Front Windscreen": [("Windshield", "synonym")],
    "Headlamp": [("Headlight", "synonym")],
    "Front Fender": [("Wing panel", "synonym")],
    "Front Number Plate": [("Front bumper number plate", "synonym")],
    "Front Wheel Arch Liner": [("Front splash shield", "synonym"), ("Front arch liner", "synonym")],
    "Front Wheel Arch Trim": [("Front wheel arch moulding", "synonym"), ("Front wheel arch molding", "synonym"),
                              ("Front mud flap", FILED_UNDER),
                              ("Front fender wheel arch ganrish", "typo")],
    "Wing Mirror": [("Side mirror", "synonym")],
    "Sill / Rocker Panel": [("Side sill", "synonym"), ("Sill panel", "synonym")],
    "Rear Bumper": [("Rear air vent", FILED_UNDER)],
    "Rear Bumper Beam": [("Rear bumper reinforcement", "synonym")],
    "Boot Lid": [("Tail gate", "synonym"), ("Tailgate", "synonym"), ("Back door", "synonym"), ("Trunk", "synonym")],
    "Boot Lid Lock": [("Boot lid catch", "synonym"), ("Key lock", FILED_UNDER)],
    "Boot Lid Weatherstrip": [("Boot lid weather strip", "synonym"), ("Boot lid weatherstripe", "typo")],
    "Boot Lid Inner Trim": [("Boot lid trim board", "synonym")],
    # The firm words a hatch's boot lid lamp "Tail gate reflector lamp": the
    # reflector is that lamp, not a part filed under it.
    "Boot Lid Lamp": [("Boot lid reflector", "synonym")],
    "Rear Windscreen": [("Rear screen", "synonym"), ("Back glass", "synonym")],
    "Tail Lamp": [("Taillamp", "typo"), ("Taillight", "synonym")],
    "Rear Quarter Panel": [("Quarter panel", "synonym")],
    # Not "Taillamp panel": history files that under Tail Lamp (its "tail ?lamp"
    # rule comes first), so as an alias it would point two ways.
    "Rear End Panel": [("Rear body panel", "synonym")],
    "Boot Floor Board": [("Tool tray", FILED_UNDER), ("Floor panel board", "synonym")],
    "Boot Side Trim": [("Quarter inner trim", "synonym")],
    "Rear Wheel Arch Liner": [("Rear splash shield", "synonym"), ("Rear arch liner", "synonym")],
    "Rear Wheel Arch Trim": [("Rear wheel arch moulding", "synonym"), ("Rear wheel arch molding", "synonym"),
                             ("Rear mud flap", FILED_UNDER)],
    "Front Wheel": [("Front rim", "synonym")],
    "Rear Wheel": [("Rear rim", "synonym")],
    "Front Tyre": [("Front tire", "synonym")],
    "Rear Tyre": [("Rear tire", "synonym")],
    "Underbody / Chassis": [("Undercarriage", "synonym"), ("Chassis", "synonym")],
    # "Front cross member" is the firm's own wording of the subframe (the
    # same part), so it never shows as an alias.
    "Front Subframe": [("Front cross member", "synonym"), ("Subframe", "synonym")],
    "Engine Undercover": [("Front bumper under cover", FILED_UNDER)],
    "Exhaust System": [("Silencer", FILED_UNDER)],
    "Suspension - Front": [("Lower arm", FILED_UNDER), ("Anti roll bar", FILED_UNDER), ("Shock absorber", FILED_UNDER)],
    "Suspension - Rear": [("Rear trailing arm", FILED_UNDER), ("Rear torsion beam", FILED_UNDER),
                          ("Rear shock absorber", FILED_UNDER)],
    # The firm prints the steering assembly as "Steering rack and pinion".
    "Steering Assembly": [("Steering rack", "synonym")],
}


def slug(name: str) -> str:
    """"Sill / Rocker Panel" -> "sill-rocker-panel"; "Door (side
    undetermined)" -> "door-side-undetermined"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def norm(text: str | None) -> str:
    """Lower case, punctuation off, single spaces: the form names are
    compared in (the dashboard's lib/partSearch.ts normalize)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


# ---------------------------------------------------------------- 3D zones


def _ts_block(src: str, name: str) -> str:
    m = re.search(rf"const {name}\s*:[^=]*=\s*\{{(.*?)\n\}};", src, re.S)
    if not m:
        raise ValueError(f"vehicleZones.ts: could not find {name}")
    return m.group(1)


def ts_tables(path: Path | str | None = ZONES_TS) -> dict:
    """The dashboard's zone tables (lib/vehicleZones.ts): CANONICAL_ZONES
    (part -> zone or None), STAND_INS (part -> where it really is) and
    EITHER_SIDE (an "any_*" zone -> the zones it may be). Read as text, the
    way scripts/check_taxonomy_sync.py reads it: the table stays where the
    3D map uses it, and the library follows it."""
    if path is None:
        raise FileNotFoundError("the dashboard's lib/vehicleZones.ts is not here (the api container holds only app/): "
                                "rebuild the library from a full checkout")
    src = Path(path).read_text(encoding="utf-8")
    zones = {name: value or None for name, value in
             re.findall(r'^\s*"([^"]+)":\s*(?:"([a-z_]+)"|null),?\s*(?://.*)?$', _ts_block(src, "CANONICAL_ZONES"),
                        re.M)}
    stand_ins = dict(re.findall(r'^\s*"([^"]+)":\s*"([^"]*)",?\s*(?://.*)?$', _ts_block(src, "STAND_INS"), re.M))
    either = {key: re.findall(r'"([a-z_]+)"', body)
              for key, body in re.findall(r"^\s*([a-z_]+):\s*\[([^\]]*)\]", _ts_block(src, "EITHER_SIDE"), re.M)}
    return {"zones": zones, "stand_ins": stand_ins, "either_side": either}


def _panels(zone: str | None, either: dict[str, list[str]]) -> list[str]:
    if not zone:
        return []
    return list(either.get(zone, [zone]))


# ---------------------------------------------------------------- the core


def _component_bases() -> list[str]:
    """Every known part without its side, in KNOWN_PARTS order: the
    components. Twins ("(side undetermined)") and Other are not."""
    out: list[str] = []
    for part in KNOWN_PARTS:
        if part == OTHER_PART or part.endswith(UNDETERMINED_SUFFIX):
            continue
        base, _ = split_side(part)
        if base not in out:
            out.append(base)
    return out


def _owned_names(base: str) -> dict[str, str]:
    """The canonical names a component owns: {"left", "right"} or {"plain"}."""
    left, right = f"Left {base}", f"Right {base}"
    if left in KNOWN_PARTS and right in KNOWN_PARTS:
        return {"left": left, "right": right}
    return {"plain": base}


def _strip_code(wording: str | None) -> str | None:
    if not wording:
        return None
    for code in SIDE_CODES.values():
        if wording.endswith(" " + code):
            return wording[: -len(code) - 1]
    return wording


def _member_base(part: str) -> str:
    return part.replace("{side} ", "").replace("{side}", "").strip()


def _body_classes(gate) -> list[str]:
    """Which of the two printed bodies (taxonomy.body_class) a car map
    body gate lets a part on: a gate passes a body it names or whose class
    it names (car_map._body_ok)."""
    if not gate:
        return ["sedan", "hatch"]
    out = []
    classes = (("sedan", ("Sedan", "Coupe")), ("hatch", ("Hatchback", "SUV", "MPV", "Van", "Wagon", "Pickup")))
    for cls, bodies in classes:
        if any(b in gate or body_class(b) in gate for b in bodies):
            out.append(cls)
    return out


def _placement(base: str) -> str:
    """How a "(side undetermined)" name is put on a side later (taxonomy.
    sided_part): "not_placed" -- a side alone doesn't say which door;
    "needs_end" -- wheels, tyres, arches need front or rear too; else
    "by_side"."""
    from app.reports.taxonomy import _NEEDS_END, _NOT_PLACED

    return "not_placed" if base in _NOT_PLACED else "needs_end" if base in _NEEDS_END else "by_side"


def _families() -> list[str]:
    """The price book's family names, in the order its rules find them
    (parts/history.py _FAMILY_RULES): what a past line is filed under."""
    from app.parts.history import _FAMILY_RULES

    out: list[str] = []
    for _, family in _FAMILY_RULES:
        if family not in out:
            out.append(family)
    return out


def _family_of(text: str | None) -> str | None:
    from app.parts.history import family_of

    return family_of(text) if text else None


def build_core(zones_ts: Path | str | None = ZONES_TS) -> dict:
    """The core library from today's code (see the module docstring). Pure
    apart from reading the code's own data files; the same code gives the
    same dict, key order included."""
    from app.intake import car_map as car_map_mod
    from app.intake import links as links_mod

    ts = ts_tables(zones_ts)
    zone_of, stand_ins, either = ts["zones"], ts["stand_ins"], ts["either_side"]
    # A line the reader didn't understand must stop the build, not turn into
    # "no mesh" (vehicleZones.ts has a decision for every known name:
    # check_taxonomy_sync.py).
    unread = [p for p in KNOWN_PARTS if p not in zone_of]
    if unread:
        raise ValueError(f"vehicleZones.ts: no zone read for {', '.join(unread[:5])}"
                         f"{' ...' if len(unread) > 5 else ''} (a line the library's reader doesn't understand?)")
    cmap = car_map_mod.load()
    new_names = car_map_mod.load_new_names()
    table = links_mod.load_table()
    if not cmap or not new_names or not table:
        raise ValueError("the car map, its new names or the link table did not load (see the log)")

    assemblies = cmap["assemblies"]
    new_by_base = {_member_base(n["name"]): n for n in new_names["names"]}
    relation_names = {_member_base(n) for r in cmap.get("relations") or [] for n in (r["parent"], r["child"])}

    def asm_area(asm: dict) -> str:
        return _ASSEMBLY_AREA.get(asm["id"], _CAR_MAP_AREA[asm["area"]])

    bases = _component_bases()
    key_of = {base: slug(base) for base in bases}

    # Which assemblies list each base, with the member record.
    listed: dict[str, list[tuple[str, dict]]] = {base: [] for base in bases}
    anchors: dict[str, list[str]] = {base: [] for base in bases}
    for asm in assemblies:
        for member in asm["members"]:
            b = _member_base(member["part"])
            if b in listed:
                listed[b].append((asm["id"], member))
        for anchor in asm.get("anchors") or []:
            b = _member_base(anchor)
            if b in anchors:
                anchors[b].append(asm["id"])

    def home_of(base: str) -> str:
        if base in _HOME:
            return _HOME[base]
        if anchors[base]:
            return anchors[base][0]
        if base in new_by_base:
            return new_by_base[base]["assembly"]
        return listed[base][0][0]

    # Twins: each "(side undetermined)" name and the components it stands for.
    twin_of: dict[str, str | None] = {}
    for base in bases:
        names = _owned_names(base)
        twin_of[base] = unsided_part(names["left"]) if "left" in names else None
    twins_cover: dict[str, list[str]] = {}
    for base in bases:
        if twin_of[base]:
            twins_cover.setdefault(twin_of[base], []).append(key_of[base])

    # The linked parts (L1): the table's names, {side} expanded, as components.
    def comp_of_link_name(name: str) -> str | None:
        b = _member_base(name)
        return key_of.get(b)

    linked_out: dict[str, list[dict]] = {key: [] for key in key_of.values()}
    linked_in: dict[str, list[dict]] = {key: [] for key in key_of.values()}
    for link in table["links"]:
        parent, child = comp_of_link_name(link["parent"]), comp_of_link_name(link["child"])
        if not parent or not child:
            continue
        facts = {"type": link["type"], "side": link["side"], "min_hit": link["min_hit"], "hidden": bool(link["hidden"]),
                 "strip_down": bool(link["strip_down"]), "area": link["area"], "reason": link["reason"]}
        linked_out[parent].append({"id": link["id"], "key": child, "name": _member_base(link["child"]), **facts})
        # The facts once, on the parent's side: an "in" names the link by id.
        linked_in[child].append({"id": link["id"], "key": parent, "name": _member_base(link["parent"]),
                                 "type": link["type"]})

    families = _families()
    canonical = set(CANONICAL_PARTS)

    components = []
    for base in bases:
        key = key_of[base]
        names = _owned_names(base)
        first = names.get("left") or names["plain"]
        home = home_of(base)
        member = next((m for a, m in listed[base] if a == home), None)
        if member is None:
            raise ValueError(f"{base}: home assembly {home} does not list it")
        also = [a for a, _ in listed[base] if a != home]
        also = list(dict.fromkeys(also))
        asm = next(a for a in assemblies if a["id"] == home)
        zones = {side: zone_of.get(name) for side, name in names.items()}
        panels = sorted({p for z in zones.values() for p in _panels(z, either)})
        if "plain" in names and (zones["plain"] or "") in either:
            side_rule = "one_side_unknown"
        else:
            side_rule = "sided" if "left" in names else "none"
        twin = twin_of[base]
        twin_base = twin[: -len(UNDETERMINED_SUFFIX)] if twin else None
        role = member.get("role", "other")
        area = asm_area(asm)
        if role == "safety":
            kind = "electrical"
        elif area == "inside":
            kind = "interior"
        else:
            kind = _ROLE_KIND.get(role, "part")
        firm = _strip_code(FIRM_PART_NAMES.get(first))
        firm_hatch = _strip_code(FIRM_PART_NAMES_HATCH.get(first))
        family = _family_of(firm) or _family_of(base)
        new = new_by_base.get(base)
        seen = {norm(base), norm(firm), norm(firm_hatch)}
        aliases = []
        candidates = [(text, kind_, "code") for text, kind_ in _ALIASES.get(base, [])]
        if new and new.get("also_check"):
            candidates.append((new["also_check"], "also_check", "car map"))
        for text, kind_, source in candidates:
            if norm(text) not in seen:
                seen.add(norm(text))
                aliases.append({"text": text, "kind": kind_, "source": source})
        if names.get("left") in canonical or names.get("plain") in canonical:
            ai_use = "reader"
        elif base in relation_names:
            ai_use = "car_map"
        else:
            ai_use = "none"
        components.append({
            "key": key,
            "name": base,
            "names": names,
            "twin": twin,
            "twin_key": slug(twin) if twin else None,
            "side_rule": side_rule,
            "twin_placement": _placement(twin_base) if twin else None,
            "area": area,
            "assembly": home,
            "also_in": also,
            "kind": kind,
            "role": role,
            "firm_wording": firm,
            "firm_wording_hatch": firm_hatch,
            "family": family,
            "parent_key": key_of.get(family) if family and family != base else None,
            "zones": zones,
            "panels": panels,
            "stand_in": stand_ins.get(first),
            "bodies": member.get("body"),
            "body_classes": _body_classes(member.get("body")),
            "hidden": bool(member.get("hidden")),
            "strip_down": bool(member.get("strip_down")),
            "if_fitted": member.get("fitted") == "if_fitted",
            "aliases": aliases,
            "linked": {"out": linked_out[key], "in": linked_in[key]},
            "ai_use": ai_use,
            "note": new.get("why") if new else None,
            "status": "core",
            "version_added": LIBRARY_VERSION,
        })

    by_key = {c["key"]: c for c in components}
    twins = []
    for part in KNOWN_PARTS:
        if not part.endswith(UNDETERMINED_SUFFIX):
            continue
        base = part[: -len(UNDETERMINED_SUFFIX)]
        covers = twins_cover.get(part, [])
        zone = zone_of.get(part)
        firm = FIRM_PART_NAMES.get(part)
        twins.append({
            "key": slug(part),
            "name": part,
            "base": base,
            "covers": covers,
            "placement": _placement(base),
            "area": by_key[covers[0]]["area"] if covers else None,
            "kind": by_key[covers[0]]["kind"] if covers else None,
            "zone": zone,
            "panels": _panels(zone, either),
            "stand_in": stand_ins.get(part),
            "firm_wording": firm,
            "firm_wording_hatch": FIRM_PART_NAMES_HATCH.get(part),
            "ai_use": "reader" if part in canonical else ("car_map" if base in relation_names else "none"),
            "status": "core",
            "version_added": LIBRARY_VERSION,
        })

    # Assemblies in the car map's order, each with the components whose home
    # it is (in KNOWN_PARTS order) and the ones it also lists.
    asm_out = []
    for asm in assemblies:
        home = [c["key"] for c in components if c["assembly"] == asm["id"]]
        also = [c["key"] for c in components if asm["id"] in c["also_in"]]
        asm_out.append({
            "key": asm["id"],
            "name": asm["name"],
            "area": asm_area(asm),
            "anchors": list(dict.fromkeys(key_of[_member_base(a)] for a in asm.get("anchors") or [])),
            "components": home,
            "also": also,
        })
    areas = [{"key": key, "label": label, "assemblies": [a["key"] for a in asm_out if a["area"] == key]}
             for key, label in AREAS]

    fam_out = []
    for family in families:
        twin = next((t["key"] for t in twins if t["base"] == family), None)
        fam_out.append({"name": family, "component": key_of.get(family), "twin": twin})

    core = {
        "format": FORMAT,
        "version": LIBRARY_VERSION,
        "core": None,
        "built_from": {
            "taxonomy": f"{len(CANONICAL_PARTS)} canonical + {len(CAR_MAP_PARTS)} car map names",
            "car_map": cmap.get("version"),
            "links": table.get("version"),
            "zones": "apps/dashboard/lib/vehicleZones.ts",
            "families": "app/parts/history.py",
        },
        "areas": areas,
        "assemblies": asm_out,
        "components": components,
        "twins": twins,
        "families": fam_out,
        "other": {"name": OTHER_PART, "firm_wording": FIRM_PART_NAMES.get(OTHER_PART)},
    }
    core["core"] = core_sha(core)
    return core


def core_sha(core: dict) -> str:
    """The core's own fingerprint ("core:<12 hex>"), over everything but
    itself: changes exactly when the code's parts change."""
    body = {k: v for k, v in core.items() if k != "core"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"core:{digest[:12]}"


def dumps(core: dict) -> str:
    """The committed file's text: one form only, so a check can compare it."""
    return json.dumps(core, indent=1, ensure_ascii=False) + "\n"


@lru_cache(maxsize=1)
def load_core(path: Path | str = CORE_PATH) -> dict:
    """The committed core (library_core.json). Cached: callers copy before
    changing anything (the API adds counts to copies)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------ look-ups


def _index(core: dict) -> tuple[dict, dict]:
    comps = {c["key"]: c for c in core["components"]}
    by_name: dict[str, tuple[str, str | None]] = {}
    for c in core["components"]:
        for side, name in c["names"].items():
            by_name[name] = (c["key"], None if side == "plain" else side)
    for t in core["twins"]:
        by_name[t["name"]] = (t["key"], None)
    return comps, by_name


def get(key: str, core: dict | None = None) -> dict | None:
    """A component or a twin by its key; None for an unknown key."""
    core = core or load_core()
    for entry in core["components"]:
        if entry["key"] == key:
            return entry
    return next((t for t in core["twins"] if t["key"] == key), None)


def component_of(part: str | None, core: dict | None = None) -> tuple[str, str | None] | None:
    """A canonical or car map part name -> (component or twin key, "left" /
    "right" / None): "Left Tail Lamp" -> ("tail-lamp", "left"). None for
    Other and any name the library doesn't hold."""
    _, by_name = _index(core or load_core())
    return by_name.get((part or "").strip())


def prints_as(key: str, side: str | None = None, body_type: str | None = None,
              core: dict | None = None) -> str | None:
    """How a component prints on Annex A for this side ("left" / "right" /
    None) and body type: the firm's wording for its body class, LH / RH
    last -- exactly taxonomy.firm_part_name for the canonical name."""
    entry = get(key, core)
    if not entry:
        return None
    hatch = body_class(body_type) == "hatch"
    wording = (entry.get("firm_wording_hatch") if hatch else None) or entry.get("firm_wording")
    if wording and side in ("left", "right") and entry.get("side_rule") == "sided":
        return f"{wording} {SIDE_CODES[side.capitalize()]}"
    return wording


def zone(key: str, side: str | None = None, core: dict | None = None) -> str | None:
    """The 3D zone a component is drawn on for that side (None: no mesh)."""
    entry = get(key, core)
    if not entry:
        return None
    if "zones" not in entry:
        return entry.get("zone")
    zones = entry["zones"]
    return zones.get(side) if side in zones else zones.get("plain")


def parent(key: str, core: dict | None = None) -> str | None:
    """The main part a component's price lines go under (its price-book
    family's component), when that is another component."""
    entry = get(key, core)
    return entry.get("parent_key") if entry else None


def tree(library: dict) -> list[dict]:
    """area -> assembly -> component -> fittings, from a library as GET
    /library answers it (counts and fittings when there are any). Each
    component sits once, under its home assembly; a fitting under every
    component it belongs to (an end-less "Sport rim" goes with both
    wheels); fittings no rule places come last, in an area of their own."""
    comps = {c["key"]: c for c in library["components"]}
    fittings_of: dict[str, list[dict]] = {}
    unplaced = []
    for f in library.get("fittings") or []:
        keys = f.get("parent_keys") or []
        if not keys:
            unplaced.append(f)
        for key in keys:
            fittings_of.setdefault(key, []).append(f)

    def slim(entry: dict) -> dict:
        out = {k: entry[k] for k in ("key", "name", "kind", "status") if k in entry}
        if "uses" in entry:
            out["uses"] = entry["uses"]
        return out

    asm_by_key = {a["key"]: a for a in library["assemblies"]}
    out = []
    for area in library["areas"]:
        assemblies = []
        for asm_key in area["assemblies"]:
            asm = asm_by_key[asm_key]
            assemblies.append({
                "key": asm["key"],
                "name": asm["name"],
                "components": [{**slim(comps[k]), "fittings": [slim(f) for f in fittings_of.get(k, [])]}
                               for k in asm["components"]],
            })
        out.append({"key": area["key"], "label": area["label"], "assemblies": assemblies})
    if unplaced:
        out.append({"key": "unplaced", "label": "Not placed yet", "assemblies": [
            {"key": "unplaced", "name": "Price-book wordings no rule places",
             "components": [], "fittings": [slim(f) for f in unplaced]}]})
    return out


# ------------------------------------------------------------ usage (staff)


def usage(case_datas, core: dict | None = None) -> dict[str, dict]:
    """{component or twin key: {"uses": cases listing it, "conditions":
    [[condition, n], ...] (the 3 most written)}} over the cases' data
    (Report.data): a case counts once per component, both sides together.
    For staff only (decision G9; the API decides)."""
    from app.reports.taxonomy import annex_condition

    _, by_name = _index(core or load_core())
    cases: Counter = Counter()
    conditions: dict[str, Counter] = {}
    for data in case_datas:
        seen = set()
        for item in ((data or {}).get("damage_summary") or []):
            if not isinstance(item, dict):
                continue
            hit = by_name.get(str(item.get("part") or "").strip())
            if not hit:
                continue
            key = hit[0]
            seen.add(key)
            cond = annex_condition(item)
            if cond:
                conditions.setdefault(key, Counter())[cond] += 1
        cases.update(seen)
    return {key: {"uses": n, "conditions": [[c, k] for c, k in conditions.get(key, Counter()).most_common(3)]}
            for key, n in cases.items()}


# ------------------------------------------------------- seed fittings


_PARENS = re.compile(r"\([^)]*\)")
_QTY = re.compile(r"\bx\s*\d+(\.\d+)?\b", re.I)
_QTY_UNITS = frozenset({"pc", "pcs", "nos", "pkt", "pkts", "set", "sets", "unit", "units"})
_EDGE = re.compile(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$")
# A number plate typed into a wording, by its shape: 1-3 capitals, a number
# and the check letter ("SBA 1234 A", Singapore's; Malaysia's when it has
# one). The price book keeps no plate since Stage 1 (owner K0, prices.
# scrub_price_book), so a line's own plate is no longer there to drop. In a
# wording not all in capitals the check letter may be missing (Malaysia's
# "WXY 1234"); in one all in capitals it must be there, so "BOOT LID 2 PCS"
# keeps its words. Only the letters go: the number is left for the word
# loop, which drops it -- and a quantity's unit after it -- as any number.
# Never part of a longer word ("L/H 2", "R.H 2" stay side words).
_PLATE = re.compile(r"(?<![0-9A-Za-z/.-])[A-Z]{1,3} ?(\d{1,4}) ?[A-Z](?![0-9A-Za-z/.-])")
_PLATE_NO_CHECK = re.compile(r"(?<![0-9A-Za-z/.-])[A-Z]{1,3} ?(\d{1,4})(?![0-9A-Za-z/.-])")
# A word naming a side, as the dashboard's lib/partSearch.ts reads them:
# LH, L/H, L.H.S., LHS, left; nearside and N/S are the left (Malaysia drives
# on the left, so the near side is the kerb side), offside and O/S the right.
_SIDE_TOKEN = re.compile(r"^(?:l\.?/?h\.?s?|r\.?/?h\.?s?|left|right|near-?side|off-?side|n/s|o/s)\.?$", re.I)


def _key_of(word: str) -> str:
    return word.replace("-", "").lower()


def _banned_words(lines) -> set[str]:
    """The words a seed name must not keep: every make and every model's
    name the price book knows (parts/history.py's own make list, the
    lines' makes and the first word of their models), and the model
    fragments taxonomy knows (MODEL_BODY_TYPES)."""
    from app.parts.history import _MAKES
    from app.reports.taxonomy import MODEL_BODY_TYPES

    words = {k for m in _MAKES for k in map(_key_of, m.split()) if len(k) >= 3}
    words |= {k for frag in MODEL_BODY_TYPES for k in map(_key_of, frag.split()) if len(k) >= 3}
    for ln in lines:
        make = [_key_of(w) for w in (getattr(ln, "make", "") or "").split()]
        model = [_key_of(w) for w in (getattr(ln, "model", "") or "").split()]
        words |= {k for k in make if len(k) >= 3}
        if model[: len(make)] == make and make:
            model = model[len(make):]
        if model and len(model[0]) >= 3 and not any(ch.isdigit() for ch in model[0]):
            words.add(model[0])
    return words


def seed_name(item: str, banned: set[str], plate: str = "") -> str | None:
    """A price-book wording as a generic name: no side words, no bracketed
    notes, no quantities, no word with a digit, no make or model name (an
    ordinary word that is also a model's -- "Seal", "Step" -- only in
    capitals, as an emblem's lettering), none of its case's plate (`plate`,
    on a line from before Stage 1) nor anything shaped as one (_PLATE).
    None when nothing is left."""
    from app.parts.history import _COMMON_WORDS

    plate_keys = {_key_of(w) for w in re.findall(r"[0-9A-Za-z]+", plate or "")}
    text = _QTY.sub(" ", _PARENS.sub(" ", item or ""))
    text = _PLATE.sub(r" \1 ", text)
    if text != text.upper():
        text = _PLATE_NO_CHECK.sub(r" \1 ", text)
    kept: list[str] = []
    dropped_number = False
    expect = None  # "hand" after "left" / "right", then "side"
    for raw in text.split():
        word = _EDGE.sub("", raw)
        key = _key_of(word)
        if not word:
            continue
        if any(ch.isdigit() for ch in word):
            dropped_number = True
            continue
        # "left hand (side)", "right hand (side)": the side words, all of them.
        if expect and key == expect:
            expect = "side" if key == "hand" else None
            continue
        expect = None
        if _SIDE_TOKEN.match(word):
            expect = "hand" if key in ("left", "right") else None
            continue
        if key in plate_keys:
            continue
        if key in _QTY_UNITS and dropped_number:
            continue
        if key in banned and (key not in _COMMON_WORDS or word.isupper()):
            continue
        dropped_number = False
        kept.append(word)
    name = " ".join(kept).strip()
    if len(norm(name)) < 3:
        return None
    return name[0].upper() + name[1:]


def _stem(word: str) -> str:
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes", "zes")):
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


def fitting_kind(text: str) -> str:
    """"fitting" when the wording names a small fitting (B.4 rule 4: clip,
    seal, retainer, emblem ...), else "part"."""
    return "fitting" if any(_stem(w) in FITTING_WORDS for w in norm(text).split()) else "part"


def _known_keys(core: dict) -> set[str]:
    """Wording keys (history.normalise_item) of everything the core already
    names: a price-book line worded as a core component is that
    component, not a fitting. A name only filed under a component ("Rear
    air vent" under the rear bumper) is not that component: such a line
    stays a seed of its own (review N1)."""
    from app.parts.history import normalise_item

    keys = set()
    for entry in core["components"] + core["twins"]:
        texts = [entry["name"], entry.get("firm_wording"), entry.get("firm_wording_hatch")]
        texts += [a["text"] for a in entry.get("aliases") or [] if a.get("kind") != FILED_UNDER]
        keys |= {normalise_item(t) for t in texts if t}
    return keys


def seed_fittings(lines, core: dict | None = None) -> list[dict]:
    """The price book's wordings as seed fittings (2A): one per wording key
    (history.normalise_item), under the component of its price-book family
    (history.family_of), kind "fitting" by the B.4 words, else "part".
    Names only: never a price, plate, case reference, make or model. A
    wording that is a core component's own (its name, firm wording or an
    alias) is that component and not repeated. `lines`: past Annex A rows
    (history.past_lines) or anything with .item, .make, .model, .plate,
    .condition, .case_ref."""
    from app.parts.history import normalise_item, side_of

    core = core or load_core()
    lines = list(lines)
    banned = _banned_words(lines)
    known = _known_keys(core)
    comps = {c["key"]: c for c in core["components"]}
    families = {f["name"]: f for f in core["families"]}
    twins = {t["key"]: t for t in core["twins"]}

    groups: dict[str, dict] = {}
    for ln in lines:
        name = seed_name(getattr(ln, "item", "") or "", banned, getattr(ln, "plate", "") or "")
        if not name:
            continue
        key = normalise_item(name)
        if not key or key in known:
            continue
        g = groups.setdefault(key, {"names": Counter(), "cases": set(), "conditions": Counter(), "sided": False})
        g["names"][name] += 1
        g["cases"].add(getattr(ln, "case_ref", None) or "")
        cond = (getattr(ln, "condition", "") or "").strip()
        if cond:
            g["conditions"][cond.capitalize() if cond.lower() in ("necessary", "serviceable") else cond] += 1
        item = getattr(ln, "item", "") or ""
        if side_of(item) or any(_SIDE_TOKEN.match(_EDGE.sub("", w)) for w in item.split()):
            g["sided"] = True

    order = {c["key"]: i for i, c in enumerate(core["components"])}
    out = []
    for key, g in groups.items():
        name = sorted(g["names"].items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        family = _family_of(name)
        fam = families.get(family) if family else None
        if fam and fam["component"]:
            parents = [fam["component"]]
        elif fam and fam["twin"]:
            parents = list(twins[fam["twin"]]["covers"])
        elif family == "Wheel Nut":
            # The one family with no canonical part (history.py): a wheel's.
            parents = list(twins.get("wheel-side-undetermined", {}).get("covers") or [])
        else:
            parents = []
        first = comps.get(parents[0]) if parents else None
        out.append({
            "key": "seed-" + slug(key),
            "name": name,
            "kind": fitting_kind(name),
            "status": "seed",
            "family": family,
            "parent_key": parents[0] if parents else None,
            "parent_keys": parents,
            "area": first["area"] if first else None,
            "assembly": first["assembly"] if first else None,
            "panels": first["panels"] if first else [],
            "sided": g["sided"],
            "aliases": [{"text": n, "kind": "wording", "source": "price book"}
                        for n in sorted(g["names"]) if n != name],
            "uses": len(g["cases"]),
            "conditions": [[c, n] for c, n in g["conditions"].most_common(3)],
        })
    out.sort(key=lambda f: (order.get(f["parent_key"], len(order)), f["kind"] != "fitting", f["name"].lower()))
    return out


def copy_core(core: dict | None = None) -> dict:
    """A deep copy of the core to add per-request facts to."""
    return copy.deepcopy(core or load_core())
