"""The parts library, core and read-only (app/parts/library.py,
app/api/library.py; L-library-cycle.md step 2A).

    python scripts/test_library.py

1. The core: the committed file is what the code gives; 7 areas, the car
   map's 25 assemblies; every known part name (taxonomy.KNOWN_PARTS: the
   102 canonical and the 45 car map names, Other aside) sits exactly once,
   as a component's own name or as a twin; every component has an area and
   a home assembly that lists it; its 3D zone is the dashboard's
   (vehicleZones.ts, read here on its own); it prints exactly as
   taxonomy.FIRM_PART_NAMES / _HATCH; aliases never point at another part's
   price-book family; every L1 link shows once out and once in.
2. Seed fittings from price-book wordings: names only (no digit runs, no
   make or model, no plate), kind by the B.4 words, placed under their
   family's part, a core part's own wording left out.
3. Usage counts and the tree.
4. The API: signed-in only; counts, conditions and seed fittings for staff
   (admin, "see all cases", "may sign off") only; nothing is written.

Offline: a temporary database, fake rows, no AI.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="library-test-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'test.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.intake import links as links_mod  # noqa: E402
from app.parts import library  # noqa: E402
from app.parts.history import family_of  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import PastAnnexLine, Report  # noqa: E402
from app.reports.taxonomy import (  # noqa: E402
    CANONICAL_PARTS,
    FIRM_PART_NAMES,
    FIRM_PART_NAMES_HATCH,
    KNOWN_PARTS,
    MODEL_BODY_TYPES,
    OTHER_PART,
    unsided_part,
)
from auth_helpers import sign_in  # noqa: E402

FAILED: list[str] = []


def check(cond, label: str, detail="") -> None:
    if cond:
        print(f"    ok   {label}")
    else:
        FAILED.append(label)
        print(f"    FAIL {label}" + (f"\n         {str(detail)[:600]}" if detail != "" else ""))


def ts_zones() -> dict[str, str | None]:
    """CANONICAL_ZONES read straight from the dashboard file, independently
    of library.ts_tables (as check_taxonomy_sync.py reads it)."""
    src = library.ZONES_TS.read_text(encoding="utf-8")
    block = re.search(r"CANONICAL_ZONES:\s*Record<string,\s*string \| null>\s*=\s*\{(.*?)\n\};", src, re.S).group(1)
    return {k: (v or None) for k, v in re.findall(r'"([^"]+)":\s*(?:"([a-z_]+)"|null)', block)}


# ---------------------------------------------------------------------------


def test_core() -> None:
    print("the core")
    built = library.build_core()
    core = library.load_core()
    check(core == built, "library_core.json is what the code gives (check_library_sync.py)")
    check(core["core"] == library.core_sha(core), "the core's fingerprint is its content's")
    check([a["key"] for a in core["areas"]] == list(library.AREA_KEYS), "7 areas in the page's order",
          [a["key"] for a in core["areas"]])
    check(len(core["assemblies"]) == 25, "the car map's 25 assemblies", len(core["assemblies"]))
    check(len(core["components"]) == 86, "86 components", len(core["components"]))
    check(len(core["twins"]) == 25, "25 twins", len(core["twins"]))

    # Every known name exactly once, as a component's own name or a twin.
    owned: list[str] = [n for c in core["components"] for n in c["names"].values()] + [t["name"] for t in core["twins"]]
    wanted = [p for p in KNOWN_PARTS if p != OTHER_PART]
    dupes = sorted({n for n in owned if owned.count(n) > 1})
    check(not dupes, "no name is held twice", dupes)
    check(sorted(owned) == sorted(wanted), "every known part name is a component or a twin, nothing else",
          sorted(set(owned) ^ set(wanted)))
    check(all(p in owned for p in CANONICAL_PARTS if p != OTHER_PART), "every CANONICAL_PARTS name is in the library")
    check(core["other"] == {"name": OTHER_PART, "firm_wording": FIRM_PART_NAMES[OTHER_PART]},
          "Other / Not Listed is the escape hatch, not a component", core["other"])
    keys = [c["key"] for c in core["components"]] + [t["key"] for t in core["twins"]]
    check(len(keys) == len(set(keys)), "keys are unique")
    check(all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", k) for k in keys), "keys are slugs")

    # Areas and assemblies.
    asm = {a["key"]: a for a in core["assemblies"]}
    cmap = json.loads((ROOT / "app/intake/car_map.json").read_text(encoding="utf-8"))
    members = {a["id"]: {m["part"].replace("{side} ", "") for m in a["members"]} for a in cmap["assemblies"]}
    bad_area = [c["key"] for c in core["components"] if c["area"] not in library.AREA_KEYS]
    check(not bad_area, "every component has one of the 7 areas", bad_area)
    bad_home = [c["key"] for c in core["components"] if c["name"] not in members.get(c["assembly"], set())]
    check(not bad_home, "every component's home assembly lists it in the car map", bad_home)
    bad_also = [c["key"] for c in core["components"] if any(c["name"] not in members[a] for a in c["also_in"])]
    check(not bad_also, "every 'also in' assembly lists it too", bad_also)
    check(all(c["area"] == asm[c["assembly"]]["area"] for c in core["components"]),
          "a component's area is its home assembly's")
    in_tree = [k for a in core["assemblies"] for k in a["components"]]
    check(sorted(in_tree) == sorted(c["key"] for c in core["components"]),
          "each component is under exactly one assembly (its home)")
    check(sorted(k for a in core["areas"] for k in a["assemblies"]) == sorted(asm),
          "each assembly is under exactly one area")
    by_key = {c["key"]: c for c in core["components"]}
    check(by_key["interior-trim"]["area"] == "inside" and by_key["roof"]["area"] == "roof"
          and by_key["front-subframe"]["area"] == "under" and by_key["front-tyre"]["area"] == "wheels",
          "the roof, under, wheels and inside areas hold their parts")
    check(by_key["radiator"]["assembly"] == "front-end" and by_key["boot-side-trim"]["assembly"] == "boot-interior",
          "hand-checked homes: the radiator in the front end, the boot side trim in the boot")
    check(set(c["kind"] for c in core["components"]) <= set(library.KINDS) - {"fitting"},
          "core kinds are panel / part / mechanical / electrical / interior (a fitting is never core)")
    check(by_key["rear-bumper"]["kind"] == "panel" and by_key["airbag-system"]["kind"] == "electrical"
          and by_key["seat"]["kind"] == "interior" and by_key["exhaust-system"]["kind"] == "mechanical",
          "kinds from the car map's roles")
    check(all(c["status"] == "core" and c["version_added"] == 0 for c in core["components"] + core["twins"]),
          "every core entry is status core, version 0")

    # Zones: the dashboard's table, for every name.
    zones = ts_zones()
    wrong = []
    for c in core["components"]:
        for side, name in c["names"].items():
            if c["zones"].get(side) != zones.get(name):
                wrong.append((name, c["zones"].get(side), zones.get(name)))
        if bool(c["panels"]) != any(c["zones"].values()):
            wrong.append((c["key"], "panels", c["panels"]))
    for t in core["twins"]:
        if t["zone"] != zones.get(t["name"]):
            wrong.append((t["name"], t["zone"], zones.get(t["name"])))
    check(not wrong, "every zone is vehicleZones.ts's own, and panels exist exactly where a zone does", wrong[:5])
    check(by_key["tail-lamp"]["panels"] == ["l_taillamp", "r_taillamp"], "a sided part's panels are both sides'")
    check(by_key["fuel-filler-flap"]["side_rule"] == "one_side_unknown"
          and by_key["fuel-filler-flap"]["panels"] == ["l_quarter", "r_quarter"],
          "the fuel flap: one side, unknown which (any_quarter)")
    check(by_key["exhaust-system"]["panels"] == [] and by_key["exhaust-system"]["zones"] == {"plain": None},
          "a part with no mesh has no panel")
    check(by_key["boot-lid-striker"]["stand_in"] == "under the boot opening", "stand-ins come with the zone")

    # Wording: exactly taxonomy's, both bodies, LH / RH last.
    wrong = []
    for c in core["components"]:
        for side, name in c["names"].items():
            s = None if side == "plain" else side
            for body, table in ((None, FIRM_PART_NAMES), ("Sedan", FIRM_PART_NAMES), ("SUV", FIRM_PART_NAMES_HATCH),
                                ("Hatchback", FIRM_PART_NAMES_HATCH)):
                got = library.prints_as(c["key"], s, body, core)
                if got != table[name]:
                    wrong.append((name, body, got, table[name]))
    for t in core["twins"]:
        want = (FIRM_PART_NAMES[t["name"]], FIRM_PART_NAMES_HATCH[t["name"]])
        if (t["firm_wording"], t["firm_wording_hatch"]) != want:
            wrong.append((t["name"], t["firm_wording"]))
    check(not wrong, "every name prints exactly as FIRM_PART_NAMES / FIRM_PART_NAMES_HATCH", wrong[:5])
    check(library.prints_as("boot-lid", None, "SUV", core) == "Tail gate assy"
          and library.prints_as("boot-lid", None, "Sedan", core) == "Boot lid assy",
          "the boot lid prints as the firm writes it per body")

    # Sides and twins.
    check(all(t["covers"] for t in core["twins"]), "every twin stands for at least one component")
    wrong = [c["key"] for c in core["components"]
             if "left" in c["names"] and c["twin"] != unsided_part(c["names"]["left"])]
    check(not wrong, "a component's twin is taxonomy.unsided_part's", wrong)
    twins = {t["key"]: t for t in core["twins"]}
    check(twins["door-side-undetermined"]["placement"] == "not_placed"
          and twins["wheel-side-undetermined"]["placement"] == "needs_end"
          and twins["tail-lamp-side-undetermined"]["placement"] == "by_side",
          "twin placement from taxonomy's _NOT_PLACED / _NEEDS_END")
    check(twins["door-side-undetermined"]["covers"] == ["front-door", "rear-door"], "a door twin covers both doors")
    check(library.component_of("Left Tail Lamp", core) == ("tail-lamp", "left")
          and library.component_of("Bonnet", core) == ("bonnet", None)
          and library.component_of("Door (side undetermined)", core) == ("door-side-undetermined", None)
          and library.component_of(OTHER_PART, core) is None,
          "component_of: a part name -> its component and side")
    check(library.zone("tail-lamp", "right", core) == "r_taillamp" and library.zone("bonnet", None, core) == "bonnet",
          "zone(key, side)")
    check(library.parent("boot-lid-striker", core) == "boot-lid-lock" and library.parent("rear-bumper", core) is None,
          "parent: the price-book family's part, when it is another part")

    # Aliases: from the code's own rules; never another part's family.
    wrong = []
    for c in core["components"]:
        for a in c["aliases"]:
            if a["kind"] in ("synonym", "typo"):
                fam = family_of(a["text"])
                if fam not in (None, c["family"], c["name"]):
                    wrong.append((c["name"], a["text"], fam))
            if library.norm(a["text"]) in (library.norm(c["name"]), library.norm(c["firm_wording"])):
                wrong.append((c["name"], a["text"], "repeats the name"))
    check(not wrong, "aliases agree with the price book's family rules and never repeat the name", wrong)
    trim = by_key["front-wheel-arch-trim"]
    check(any(a["kind"] == "typo" and "ganrish" in a["text"] for a in trim["aliases"]),
          "the firm's 'ganrish' typo is an alias of the wheel arch garnish")
    check(any(a["kind"] == "also_check" for a in by_key["front-bumper-absorber"]["aliases"]),
          "the car map's 'also check' words are aliases")

    # Linked parts: L1, each link once out and once in.
    table = links_mod.load_table()
    outs = sorted(link["id"] for c in core["components"] for link in c["linked"]["out"])
    ins = sorted(link["id"] for c in core["components"] for link in c["linked"]["in"])
    want = sorted(link["id"] for link in table["links"])
    check(outs == want and ins == want, "every L1 link shows once out (on the parent) and once in (on the child)",
          (len(outs), len(ins), len(want)))
    check(any(x["key"] == "rear-bumper-beam" for x in by_key["rear-bumper"]["linked"]["out"]),
          "the rear bumper links out to its beam")
    check(by_key["front-bumper"]["ai_use"] == "reader" and by_key["front-bumper-absorber"]["ai_use"] == "links",
          "ai_use: canonical names are read by the AI, car map names only through linked parts")


# ---------------------------------------------------------------------------


def line(item, make="Honda", model="Vezel 1.5X", plate="SXX1234A", condition="Necessary", ref="CL 1"):
    return SimpleNamespace(item=item, make=make, model=model, plate=plate, condition=condition, case_ref=ref)


SEED_LINES = [
    line("Rear bumper side retainer LH", ref="CL 1"),
    line("Rear bumper side retainer RH", ref="CL 2"),
    line("Rear bumper clips x 10", ref="CL 1"),
    line("Rear bumper clips (10 pcs)", ref="CL 2"),
    line("Tail gate emblem VEZEL", ref="CL 1"),
    line("Boot lid emblem CIVIC", make="Honda", model="Civic 1.5", ref="CL 3"),
    line("Boot lid emblem 1.6", make="Honda", model="Civic 1.5", ref="CL 3"),
    line("Tail gate stopper", ref="CL 1"),
    line("Tailgate stopper", ref="CL 2"),
    line("Tail gate assy", condition="Buckled", ref="CL 1"),       # the boot lid itself (hatch wording)
    line("Rear bumper fascia", condition="Bent", ref="CL 2"),      # the rear bumper itself
    line("Tail lamp LH", condition="Cracked", ref="CL 2"),         # the tail lamp itself
    line("Sport rim", condition="Grazed", ref="CL 3"),
    line("Wheel nut", ref="CL 3"),
    line("Body panel joint sealer", ref="CL 3"),                   # no family rule places it
    line("Front bumper lower garnish SXX 1234 A", plate="SXX 1234 A", ref="CL 4"),  # a plate typed in it
]


def test_seeds() -> None:
    print("seed fittings (price-book wordings, names only)")
    core = library.load_core()
    seeds = library.seed_fittings(SEED_LINES, core)
    by_name = {s["name"]: s for s in seeds}
    banned = {"vezel", "civic", "honda", "sxx1234a", "sxx"} | {k for frag in MODEL_BODY_TYPES for k in frag.split()
                                                        if len(k) >= 3}
    names = [s["name"] for s in seeds] + [a["text"] for s in seeds for a in s["aliases"]]
    check(not [n for n in names if re.search(r"\d", n)], "no seed name holds a digit", names)
    check(not [n for n in names if set(re.findall(r"[a-z0-9-]+", n.lower())) & banned],
          "no seed name holds a make, model or plate", names)
    check(all(s["status"] == "seed" for s in seeds), "every seed is status seed")
    retainer = by_name.get("Rear bumper side retainer")
    check(retainer and retainer["parent_key"] == "rear-bumper" and retainer["sided"] and retainer["kind"] == "fitting",
          "a retainer: one sided fitting under the rear bumper, side words gone", retainer)
    clips = by_name.get("Rear bumper clips")
    check(clips and clips["kind"] == "fitting" and clips["uses"] == 2 and clips["conditions"] == [["Necessary", 2]],
          "clips: quantities and brackets dropped, two cases, Necessary", clips)
    check(by_name.get("Tail gate emblem", {}).get("parent_key") == "boot-lid"
          and by_name.get("Boot lid emblem", {}).get("kind") == "fitting",
          "emblems lose the model's lettering and sit under the boot lid", sorted(by_name))
    stoppers = [s for s in seeds if "stopper" in s["name"].lower()]
    stopper = stoppers[0] if len(stoppers) == 1 else None
    check(stopper and sorted([stopper["name"]] + [a["text"] for a in stopper["aliases"]])
          == ["Tail gate stopper", "Tailgate stopper"],
          "one wording key, one seed: the other spelling is its alias", stoppers)
    check(stopper and stopper["kind"] == "fitting", "a stopper is a fitting (B.4 words)")
    check(not {"Tail gate assy", "Rear bumper fascia", "Tail lamp", "Tail lamp LH"} & set(by_name),
          "a core part's own wording is that part, not a seed", sorted(by_name))
    check(by_name.get("Sport rim", {}).get("parent_keys") == ["front-wheel", "rear-wheel"]
          and by_name["Sport rim"]["kind"] == "part",
          "an end-less rim goes with both wheels (as prefill reads it)", by_name.get("Sport rim"))
    check(by_name.get("Wheel nut", {}).get("parent_keys") == ["front-wheel", "rear-wheel"]
          and by_name["Wheel nut"]["kind"] == "fitting",
          "the wheel nut (no canonical part) goes with the wheels", by_name.get("Wheel nut"))
    sealer = by_name.get("Body panel joint sealer")
    check(sealer and sealer["parent_key"] is None and sealer["parent_keys"] == [] and sealer["area"] is None,
          "a wording no rule places has no parent", sealer)
    check("Front bumper lower garnish" in by_name, "a plate in a wording is dropped, the rest kept", sorted(by_name))
    check(not any(k in s for s in seeds for k in ("price", "adjusted_cost", "workshop_est", "plate", "case_ref",
                                                    "make", "model")),
          "a seed carries no price, plate, case, make or model field")
    check(library.fitting_kind("Rear windscreen glass clips") == "fitting"
          and library.fitting_kind("Front bumper side bracket") == "part"
          and library.fitting_kind("Door seals") == "fitting" and library.fitting_kind("Bushes") == "fitting",
          "fitting_kind: the B.4 words, plural too")
    check(library.seed_name("Tail gate emblem FIT", {"fit"}, "") == "Tail gate emblem"
          and library.seed_name("Re-fit kit", {"fit"}, "") == "Re-fit kit"
          and library.seed_name("Door seal", {"seal"}, "") == "Door seal",
          "an ordinary word that is a model's name goes only in an emblem's capitals")


def test_usage_and_tree() -> None:
    print("usage counts and the tree")
    core = library.load_core()
    datas = [
        {"damage_summary": [{"part": "Left Tail Lamp", "condition": "Cracked"},
                            {"part": "Right Tail Lamp", "condition": "Cracked"},
                            {"part": "Boot Lid", "condition_tokens": ["Dented"]}]},
        {"damage_summary": [{"part": "Left Tail Lamp", "condition": "Grazed/cracked"},
                            {"part": "Door (side undetermined)"}, {"part": OTHER_PART, "other_part_name": "Badge"}]},
        {"damage_summary": "not a list"},
        None,
    ]
    counts = library.usage(datas, core)
    check(counts["tail-lamp"]["uses"] == 2, "a case counts once per component, both sides together",
          counts.get("tail-lamp"))
    check(counts["tail-lamp"]["conditions"] == [["Cracked", 2], ["Grazed/cracked", 1]],
          "the conditions written most", counts["tail-lamp"]["conditions"])
    check(counts["boot-lid"] == {"uses": 1, "conditions": [["Dented", 1]]}, "a condition from the words",
          counts["boot-lid"])
    check(counts["door-side-undetermined"]["uses"] == 1, "a twin is counted as itself")
    check(set(counts) == {"tail-lamp", "boot-lid", "door-side-undetermined"}, "Other and bad data count nothing",
          set(counts))

    lib = library.copy_core(core)
    lib["fittings"] = library.seed_fittings(SEED_LINES, core)
    tree = library.tree(lib)
    check([a["key"] for a in tree] == list(library.AREA_KEYS) + ["unplaced"], "the tree: 7 areas, then the unplaced",
          [a["key"] for a in tree])
    placed = [c["key"] for a in tree for s in a["assemblies"] for c in s["components"]]
    check(sorted(placed) == sorted(c["key"] for c in core["components"]), "every component once in the tree")
    rear = next(a for a in tree if a["key"] == "rear")
    bumper = next(s for s in rear["assemblies"] if s["key"] == "rear-bumper")
    rb = next(c for c in bumper["components"] if c["key"] == "rear-bumper")
    check({"Rear bumper side retainer", "Rear bumper clips"} <= {f["name"] for f in rb["fittings"]},
          "fittings sit under their part", rb["fittings"])
    wheels = next(a for a in tree if a["key"] == "wheels")
    under = {c["key"]: [f["name"] for f in c["fittings"]] for s in wheels["assemblies"] for c in s["components"]}
    check("Sport rim" in under["front-wheel"] and "Sport rim" in under["rear-wheel"], "a two-parent seed under both")
    check(tree[-1]["assemblies"][0]["fittings"][0]["name"] == "Body panel joint sealer", "the unplaced come last")
    lib["components"][0]["uses"] = 5
    check(library.tree(lib)[0]["assemblies"][0]["components"][0].get("uses") == 5, "the tree carries counts when given")
    check("uses" not in library.tree(library.copy_core(core))[0]["assemblies"][0]["components"][0],
          "and none when not")
    check(library.load_core()["components"][0].get("uses") is None, "the cached core is never changed")


# ---------------------------------------------------------------------------


def test_api() -> None:
    print("GET /library, /library/tree")
    init_db()
    db = SessionLocal()
    try:
        db.add(Report(channel="manual", reporter_chat_id="dashboard", status="Pending Review",
                      data={"damage_summary": [{"part": "Left Tail Lamp", "condition": "Cracked"},
                                               {"part": "Rear Bumper", "condition": "Bent"}]}))
        db.add(Report(channel="manual", reporter_chat_id="dashboard", status="Signed Off",
                      data={"damage_summary": [{"part": "Right Tail Lamp", "condition": "Cracked"}]}))
        # A draft never counts.
        db.add(Report(channel="manual", reporter_chat_id="dashboard", status="intake",
                      data={"damage_summary": [{"part": "Bonnet"}]}))
        for item, ref in (("Rear bumper side retainer LH", "CL 11"), ("Rear bumper clips", "CL 11"),
                          ("Tail gate emblem VEZEL", "CL 12")):
            db.add(PastAnnexLine(case_ref=ref, source_file="x.xlsx", plate="SXX1234A", make="Honda", model="Vezel",
                                 make_model_raw="Honda Vezel", item=item, item_key=item.lower(), condition="Necessary",
                                 qty=2, workshop_est=123.0, adjusted_cost=98.0))
        db.commit()
        before = (db.query(Report).count(), db.query(PastAnnexLine).count())
    finally:
        db.close()

    anon = TestClient(app)
    r = anon.get("/library")
    check(r.status_code == 401, "signed out: 401", r.status_code)

    def client(**profile):
        c = TestClient(app)
        sign_in(c, **profile)
        return c

    plain = client(role="user")
    r = plain.get("/library")
    check(r.status_code == 200, "a plain account may read the library", r.status_code)
    body = r.json()
    check(body["staff"] is False and body["fittings"] == [], "not staff: no seed fittings", body.get("fittings"))
    check(not any("uses" in c or "conditions" in c for c in body["components"] + body["twins"]),
          "not staff: no counts, no conditions")
    check(len(body["components"]) == 86 and len(body["areas"]) == 7 and len(body["assemblies"]) == 25,
          "the shape: components, areas, assemblies")
    check(body["version"] == 0 and body["core"] == library.load_core()["core"], "version 0 and the core's fingerprint")
    tree = plain.get("/library/tree")
    check(tree.status_code == 200 and [a["key"] for a in tree.json()["areas"]] == list(library.AREA_KEYS),
          "the tree for a plain account (no unplaced: no seeds)", tree.status_code)
    check("uses" not in json.dumps(tree.json()), "the tree has no counts for a plain account")

    for label, profile in (("an admin", {"role": "admin"}),
                           ("a 'see all cases' account", {"role": "user", "sees_all_cases": True}),
                           ("a 'may sign off' account", {"role": "user", "may_sign_off": True})):
        c = client(**profile)
        body = c.get("/library").json()
        comps = {x["key"]: x for x in body["components"]}
        check(body["staff"] is True and comps["tail-lamp"]["uses"] == 2 and comps["rear-bumper"]["uses"] == 1
              and comps["bonnet"]["uses"] == 0,
              f"{label}: counts over filed cases (drafts left out)",
              (comps["tail-lamp"].get("uses"), comps["bonnet"].get("uses")))
        check(comps["tail-lamp"]["conditions"] == [["Cracked", 2]], f"{label}: typical conditions")
        names = {f["name"] for f in body["fittings"]}
        check({"Rear bumper side retainer", "Rear bumper clips", "Tail gate emblem"} <= names,
              f"{label}: the price book's wordings as seed fittings", names)
        text = json.dumps(body["fittings"])
        check(not re.search(r"SXX|1234|123\.0|98\.0|CL 1|Vezel|Honda|x\.xlsx", text),
              f"{label}: no plate, price, case, file, make or model in the fittings", text[:300])
        t = c.get("/library/tree").json()
        rear = next(a for a in t["areas"] if a["key"] == "rear")
        rb = next(x for s in rear["assemblies"] for x in s["components"] if x["key"] == "rear-bumper")
        check(rb["uses"] == 1 and "Rear bumper clips" in [f["name"] for f in rb["fittings"]],
              f"{label}: the tree with counts and fittings", rb)

    db = SessionLocal()
    try:
        after = (db.query(Report).count(), db.query(PastAnnexLine).count())
    finally:
        db.close()
    check(before == after, "reading the library writes nothing", (before, after))
    check(plain.post("/library").status_code in (404, 405), "no way to write: POST is refused")


def main() -> int:
    test_core()
    test_seeds()
    test_usage_and_tree()
    test_api()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED:\n  " + "\n  ".join(FAILED))
        return 1
    print("all library checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
