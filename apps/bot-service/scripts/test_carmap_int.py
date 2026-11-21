"""The car map joined up (I3; plans/2026-11-18-car-knowledge): the light
packs (B1), the car map's data (K1, K1b), the assembly walk (K3b) and the
review screen (K4) on one branch -- offline: Gemini is the fake of
scripts/test_c10_integration.py, no server, made-up cases only.

  1. settings: every new setting defaults to today's and is passed through
     docker-compose.prod.yml once, in the block both containers share,
     with config's default; the names switch (N1's) is off if present;
  2. everything off, the requests: the sort, the main read (intake and the
     Telegram bot), every zone read and the linked check send byte for
     byte what master 99aaa85 sends (B1's snapshots, taken there) -- at
     the defaults, and with the car map on and the review knobs off their
     defaults while the walk stays off (the requests don't depend on them);
  3. everything off, the linked parts: links.build_all and for_parent,
     called through the seam as second_look._store and relinked call them
     at the defaults (car_map.link_tables, assembly_walk.from_settings --
     even with a walk stored on the draft), give what master's links.py
     gives, field for field, on K4's sweep;
  4. everything off, end to end: one case through every route (read,
     decisions, autosave, filing, the case page, sign-off and its
     AI-accuracy row) gives the same requests and stored data as master
     99aaa85, in single mode, contract 9's zone mode, and zone mode with
     the linked parts on (and the linked check) or in shadow -- the same
     scenario run on a git archive of master and on this tree;
  5. everything on, end to end: car map K1.0, the walk with hops 2,
     strip-down out, prior order: a fake zone read answering signs yes
     gives grouped linked parts, a lifted beam, a second hop in the folded
     block, no side from the walk, every cap held; a hand-add re-links
     with the stored walk (wizard and case page); filing relabels the
     walk; sign-off counts the car map's groups;
  6. the replay reads a run's stored walk (and --no-walk / a flag with
     nothing to act on);
  7. the benchmark's car map flags: parsing, refusals, the map put in
     place, ai_settings;
  8. the AI-accuracy record: the car map's counts only on a case that has
     them; older rows and the sum as before;
  9. the seams between the walk and the review screen: a second hop's rule
     known to review_lookup (its chain's first relation's group, sided by
     the anchor, no prior, the end part's "if fitted"); it sorts after every
     first hop in either order; its own slot in the per-group cap, so the
     group's first hops never crowd it out of the block; a door's chain.

    python scripts/test_carmap_int.py
    python scripts/test_carmap_int.py --probe OUT.json   (section 4's
        scenarios alone; C10_TEST_ROOT=<a bot-service checkout> runs them
        on that tree, as section 4 does with master's)
"""
import ast
import copy
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(1, str(HERE))
for _name in ("INTAKE_CAR_MAP", "INTAKE_LINKS_ORDER", "INTAKE_LINKS_ASSEMBLY_MAX", "INTAKE_LINKS_STRIPDOWN_CAP",
              "INTAKE_LINKS_STRIPDOWN_MAX", "INTAKE_ASSEMBLY_WALK", "INTAKE_WALK_RAISE", "INTAKE_LINK_HOPS",
              "INTAKE_READ_PACKS", "INTAKE_READ_PROMPT", "INTAKE_READ_ANCHOR", "INTAKE_ZONE_PACKS",
              "INTAKE_ZONE_PHOTO_HALF", "INTAKE_SORT_PROMPT", "GEMINI_THINKING_LEVEL_SORT", "INTAKE_LINK_SKILL",
              "INTAKE_LINK_PROMPT", "INTAKE_LINK_SIGNS", "INTAKE_LINK_ORDER", "INTAKE_CAR_MAP_NAMES"):
    os.environ.pop(_name, None)

# The fake Gemini, the scratch database and the routes' helpers (C10_TEST_ROOT
# picks the tree the app is imported from).
import test_c10_integration as t  # noqa: E402

PROBE_MODES = {
    "single": {},
    "zones": dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
                  intake_check_mode="lite"),
    "links_on": dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
                     intake_check_mode="lite", intake_links="on", intake_link_check="lite"),
    "links_shadow": dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
                         intake_links="shadow", intake_link_check="lite"),
}


def probe(out: Path) -> int:
    """Section 4's scenarios on whichever tree the app came from."""
    t.init_db()
    t.OFF_MODES.update(PROBE_MODES)
    import importlib.util

    import app

    # Which tree the app came from: master has no assembly walk.
    result = {"_tree": {"app": str(Path(app.__file__).resolve().parent),
                        "has_walk": importlib.util.find_spec("app.intake.assembly_walk") is not None}}
    for mode in PROBE_MODES:
        artefacts, facts = t.off_scenario(mode)
        result[mode] = {"artefacts": artefacts, "facts": facts}
    out.write_text(json.dumps(result, sort_keys=True, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__" and "--probe" in sys.argv:
    sys.exit(probe(Path(sys.argv[sys.argv.index("--probe") + 1])))

# ------------------------------------------------------------- this tree only

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import assembly_walk, car_map, links, zones  # noqa: E402
from app.reports import accuracy, suggestions  # noqa: E402
from app.reports.models import AiAccuracy, IntakeSession, Report  # noqa: E402
from app.reports.schema import SecondLookItem  # noqa: E402
from app.reports.taxonomy import UNDETERMINED_SUFFIX, split_side  # noqa: E402
from auth_helpers import sign_in  # noqa: E402

ROOT = HERE.parent
REPO = ROOT.parent.parent
MASTER = "99aaa85"
L1 = links.TABLE
FROZEN_K1 = "21298e9ef6ced02a8c8b24fb8222f74e104cc79f4d7f5281623ae38719ee39ba"
PY = sys.executable

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {str(why)[:1500]}" if why != "" else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    t.REQUESTS.clear()
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


Settings = t.Settings


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not (k.startswith("INTAKE_") or k.startswith("GEMINI_")
                                                       or k in ("C10_TEST_ROOT", "DATABASE_URL", "STORAGE_DIR",
                                                                "SECRETS_DIR"))}
    env.update(PYTHONIOENCODING="utf-8", **extra)
    return env


# ------------------------------------------------------------- 1. settings

# Every setting the car-map work added (B1, K3b, K1/K4), with today's value.
NEW_SETTINGS = {
    # B1, the light packs
    "intake_read_packs": "", "intake_read_prompt": "v1", "intake_read_anchor": False, "intake_zone_packs": "",
    "intake_zone_photo_half": False, "intake_sort_prompt": "v2", "gemini_thinking_level_sort": None,
    "gemini_thinking_level_link": None, "intake_link_skill": "L1", "intake_link_prompt": "v1",
    "intake_link_signs": False, "intake_link_order": "candidates_first",
    # K3b, the assembly walk
    "intake_assembly_walk": "off", "intake_walk_raise": "one", "intake_link_hops": 1,
    # K1 / K4, the car map and the review screen
    "intake_car_map": "off", "intake_links_order": "table", "intake_links_assembly_max": 4,
    "intake_links_stripdown_cap": "in", "intake_links_stripdown_max": 12,
}


def compose_default(value) -> str:
    """A default as docker-compose.prod.yml writes it."""
    return "" if value is None else "true" if value is True else "false" if value is False else str(value)


def settings_section() -> None:
    fields = config.Settings.model_fields
    fresh = config.Settings()
    check({k: getattr(fresh, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "every new setting defaults to today's",
          {k: getattr(fresh, k) for k in NEW_SETTINGS if getattr(fresh, k) != NEW_SETTINGS[k]})
    check({k: getattr(settings, k) for k in NEW_SETTINGS} == NEW_SETTINGS, "and this process runs at them")
    names = fields.get("intake_car_map_names")
    check(names is None or names.default in (False, "off"),
          "the new part names (N1's intake_car_map_names) stay off: not here yet, or off by default")
    compose = (REPO / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    anchor, services = compose.index("x-bot-service: &bot-service"), compose.index("\nservices:")
    bad = []
    for name, default in NEW_SETTINGS.items():
        env = name.upper()
        want = f"    - {env}=${{{env}:-{compose_default(default)}}}"
        lines = [ln for ln in compose.splitlines() if ln.startswith(f"    - {env}=")]
        at = compose.find(f"- {env}=")
        if lines != [want] or not anchor < at < services:
            bad.append((env, lines, want))
    check(not bad, f"docker-compose.prod.yml: each of the {len(NEW_SETTINGS)} once, in the x-bot-service block the "
                   "api and the Telegram bot share, with config's default", bad)
    for service in ("telegram-bot:", "api:"):
        block = compose[compose.index(f"  {service}"):]
        block = block[:block.index("\n\n")]
        check("<<: *bot-service" in block and "environment:" not in block,
              f"{service[:-1]} takes the shared environment whole (no list of its own to replace it)")
    check(car_map.sha256_lf() == FROZEN_K1 and isinstance(car_map.MAP, dict) and car_map.MAP.get("version") == "K1.0",
          "car_map.json is K1.0, byte for byte the freeze (K1-freeze.md), and loads")


# ------------------------------------------------------------- 2. the requests


def _hashes(**env) -> dict:
    proc = subprocess.run([PY, str(HERE / "test_light_packs.py"), "--hashes"], capture_output=True, text=True,
                          encoding="utf-8", cwd=str(ROOT), env=clean_env(**env), timeout=600)
    out = {}
    for line in proc.stdout.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith('"') and '": "' in line:
            k, v = line.split('": "', 1)
            out[k.strip('"')] = v.strip('"')
    return out


def _master_hashes() -> dict:
    tree = ast.parse((HERE / "test_light_packs.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(n, "id", None) == "MASTER" for n in node.targets):
            return ast.literal_eval(node.value)
    return {}


def requests_section() -> None:
    master = _master_hashes()
    check(len(master) == 20, "B1's 20 request snapshots, taken on master 99aaa85")
    got = _hashes()
    check(got == master, "every switch off: sort, main read (intake, Telegram), 5 zones x 3 modes, linked check x 5 "
                         "-- byte for byte master's", {k: v for k, v in got.items() if master.get(k) != v})
    got = _hashes(INTAKE_CAR_MAP="on", INTAKE_LINKS_ORDER="prior", INTAKE_LINKS_ASSEMBLY_MAX="3",
                  INTAKE_LINKS_STRIPDOWN_CAP="out", INTAKE_LINKS_STRIPDOWN_MAX="6", INTAKE_WALK_RAISE="full",
                  INTAKE_LINK_HOPS="2")
    check(got == master, "car map on, review knobs off their defaults, raise full, hops 2 -- the walk off: still "
                         "master's requests", {k: v for k, v in got.items() if master.get(k) != v})
    check(zones.light_options() == {} and zones.walk_index() is None,
          "with the car map loaded, the walk off: no zone call gets anything new")


# ------------------------------------------------------------- 3. the linked parts

# A stored walk (a draft read with the walk on, the switch turned off since).
STORED_WALK = {"Rear Bumper": {"hit_at": "corner", "signs": {
    "rb-pushed": {"answer": "yes", "photo": "@x", "zone": "zone:rear"},
    "rb-corner": {"answer": "yes", "photo": "@x", "zone": "zone:rear"}}}}


def links_section() -> None:
    env = dict(os.environ)
    try:
        import test_carmap_review as k4  # its sweep and master's links.py; the app is loaded already
    finally:
        os.environ.clear()
        os.environ.update(env)

    master = k4._master_links()
    if not check(master is not None, f"master's links.py ({MASTER}) read from git"):
        return
    vehicle = {"make": "Honda", "model": "Civic"}
    tables = car_map.link_tables(links.TABLE, vehicle)
    check(tables.shown is links.TABLE and tables.shown_info is None and tables.shadow is None,
          "the seam at the defaults: L1 itself, no facts, no shadow table (K1.0 loaded all the same)")
    walk = assembly_walk.from_settings({"assembly_walk": STORED_WALK}, settings)
    check(walk is None, "a walk stored on the draft, the switch off: none")
    extra = {"walk": walk} if walk else {}
    base = links.Options(**{**links.options().__dict__, "mode": "on"})
    variants = [base, links.Options(**{**base.__dict__, "side": "strict"}),
                links.Options(**{**base.__dict__, "intact": "drop"}),
                links.Options(**{**base.__dict__, "max_items": 3}),
                links.Options(**{**base.__dict__, "catalogue": True})]
    neighbours = {"Rear Bumper": [{"part": "Rear Bumper Beam", "diagram": "REAR BUMPER"}],
                  "Front Bumper": [{"part": "Front Grille", "diagram": "GRILLE"}]}
    n, differ = 0, []
    for opts in variants:
        mopts = master.Options(**{k: v for k, v in opts.__dict__.items() if k in master.Options.__dataclass_fields__})
        for listed, others, photos, intact, dismissed in k4._sweep_cases():
            args = (copy.deepcopy(listed), copy.deepcopy(others), copy.deepcopy(photos), copy.deepcopy(intact))
            mine = links.build_all(*args, tables.shown, opts, dismissed=dismissed, neighbours=neighbours,
                                   info=tables.shown_info, **extra)
            theirs = master.build_all(*copy.deepcopy(args), L1, mopts, dismissed=dismissed, neighbours=neighbours)
            n += 1
            if json.dumps(mine) != json.dumps(theirs):
                differ.append([it["part"] for it in listed])
    check(not differ and n > 1000, f"build_all through the seam == master's on {n} builds", differ[:3])
    relinks, differ = 0, []
    for listed, others, photos, intact, dismissed in k4._sweep_cases()[::5]:
        for added in listed:
            case = {"damage_summary": copy.deepcopy(listed),
                    "second_look": {"items": copy.deepcopy(others), "dismissed": list(dismissed), "decisions": []}}
            mine = links.for_parent(copy.deepcopy(added), copy.deepcopy(case), photos, tables.shown, base,
                                    zone_intact=intact, info=tables.shown_info, **extra)
            mopts = master.Options(**{k: v for k, v in base.__dict__.items() if k in master.Options.__dataclass_fields__})
            theirs = master.for_parent(copy.deepcopy(added), copy.deepcopy(case), photos, L1, mopts, zone_intact=intact)
            relinks += 1
            if mine != theirs:
                differ.append(added["part"])
    check(not differ and relinks > 50, f"for_parent == master's on {relinks} re-links", differ[:3])
    # suggestions.relinked: an AI draft with a walk, the switch off -> as without it.
    data = {"damage_summary": [t_item("Rear Bumper", "Crumpled")], "vehicle_info": vehicle}
    look = {"items": [], "dismissed": [], "decisions": []}
    with Settings(intake_links="on"):
        plain = suggestions.relinked(t_item("Boot Lid", "Dented"), data, look)
        walked = suggestions.relinked(t_item("Boot Lid", "Dented"), data, look, {"assembly_walk": STORED_WALK})
    check(plain and plain == walked, "relinked with a stored walk, the walk off: what it gives without one")


def t_item(part: str, *tokens, **more) -> dict:
    return {"part": part, "condition_tokens": list(tokens), "ai_confidence": "High", **more}


# ------------------------------------------------------------- 4. end to end == master


def _first_diff(a, b, path="") -> str:
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k}: only on {'this tree' if k in a else 'master'}"
            if a[k] != b[k]:
                return _first_diff(a[k], b[k], f"{path}/{k}")
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} entries"
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return _first_diff(x, y, f"{path}[{i}]")
    return f"{path}: {json.dumps(a)[:300]} vs {json.dumps(b)[:300]}"


def end_to_end_off() -> None:
    work = Path(tempfile.mkdtemp(prefix="carmap-int-master-"))
    tar = work / "master.tar"
    made = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", "-o", str(tar), MASTER, "apps/bot-service"],
                          capture_output=True, text=True)
    if not check(made.returncode == 0, f"master {MASTER}'s bot-service from git (archive)", made.stderr[-300:]):
        return
    with tarfile.open(tar) as archive:
        archive.extractall(work / "tree", filter="data")
    master_root = work / "tree" / "apps" / "bot-service"
    outs = {}
    for name, root in (("master", master_root), ("here", ROOT)):
        out = work / f"{name}.json"
        proc = subprocess.run([PY, str(HERE / "test_carmap_int.py"), "--probe", str(out)], capture_output=True,
                              text=True, encoding="utf-8", cwd=str(root), env=clean_env(C10_TEST_ROOT=str(root)),
                              timeout=1800)
        if not check(proc.returncode == 0 and out.exists(), f"the scenarios ran on {name}'s tree",
                     (proc.stdout + proc.stderr)[-1500:]):
            return
        outs[name] = json.loads(out.read_text(encoding="utf-8"))
        tree = outs[name].get("_tree") or {}
        check(Path(tree.get("app", "")).parent == root.resolve() and tree.get("has_walk") is (name == "here"),
              f"... with {name}'s own app ({'with' if name == 'here' else 'no'} assembly walk)", tree)
    for mode, settings_of in PROBE_MODES.items():
        mine, theirs = outs["here"][mode], outs["master"][mode]
        facts = mine["facts"]
        check(facts.get("stage") == "ready" and facts.get("sign_off") == 200, f"{mode}: read, filed and signed off")
        check(mine == theirs, f"{mode} ({', '.join(f'{k}={v}' for k, v in settings_of.items()) or 'the defaults'}): "
                              "every request and the stored data byte for byte as master 99aaa85's",
              _first_diff(mine, theirs))
    linked = [it for it in (outs["here"]["links_on"]["artefacts"]["case"].get("second_look") or {}).get("items") or []
              if it.get("source") == "link"]
    check(linked and not any(k in it for it in linked for k in ("assembly", "hop", "folded", "signs_yes", "prior")),
          "links on at the defaults: linked parts, none with a car map field")


# ------------------------------------------------------------- 5. everything on

ON_ALL = dict(intake_read_mode="zones", intake_zone_only="suggest", intake_zone_evidence="reason",
              intake_links="on", intake_car_map="on", intake_assembly_walk="on", intake_walk_raise="one",
              intake_link_hops=2, intake_links_stripdown_cap="out", intake_links_order="prior")
# The rear zone's walk: the bumper hit at a corner, pushed in, its corner
# closed (unlocks the beam's second hop), the lid gap not closed, the boot
# floor not seen; the left tail lamp pushed in.
WALK = {"Rear Bumper": ("corner", [("rb-pushed", "yes", "P03"), ("rb-corner", "yes", "P03"),
                                   ("rb-lid-gap", "no", None), ("rb-floor", "cannot_see", None)]),
        "Left Tail Lamp": ("corner", [("tl-pushed", "yes", "P04")])}
_ZONE = t.ANSWERS["zone"]


def _zone_with_walk(kwargs, labels):
    out = _ZONE(kwargs, labels)
    props = kwargs["response_format"]["schema"]["properties"]
    if "assembly_walk" in props:
        offered = props["assembly_walk"]["items"]["properties"]["panel"]["enum"]
        damaged = {d["part"] for d in out.get("damage") or []}
        out["assembly_walk"] = [
            {"panel": panel, "hit_at": hit_at,
             "signs": [{"sign": s, "answer": a, "photo": p if p in labels else None} for s, a, p in signs]}
            for panel, (hit_at, signs) in WALK.items() if panel in offered and panel in damaged]
    return out


def _slot(it) -> tuple:
    return links.cap_slot(it)


def end_to_end_on() -> None:
    t.ANSWERS["zone"] = _zone_with_walk
    try:
        _end_to_end_on()
    finally:
        t.ANSWERS["zone"] = _ZONE


def _end_to_end_on() -> None:
    with Settings(**ON_ALL), TestClient(t._app()) as client:
        sign_in(client, role="admin", may_sign_off=True)
        rid, pids = t.new_draft(client)
        label_id = {f"P{i + 1:02d}": pid for i, pid in enumerate(pids)}
        state = t.read(client, rid)
        check(state["stage"] == "ready", "the read with everything on ends ready", state.get("error"))
        rear = [r for r in t.REQUESTS if t.kind_of(r) == "zone" and "the car's rear" in r["system_instruction"]]
        walk_field = rear and rear[0]["response_format"]["schema"]["properties"].get("assembly_walk")
        check(bool(walk_field) and "Rear Bumper" in walk_field["items"]["properties"]["panel"]["enum"],
              "the rear zone read asks the walk (K1.0's rear bumper signs) in the same call")
        session = t.db_row(IntakeSession, rid)
        walk = (session.ai_draft or {}).get("assembly_walk") or {}
        check(walk.get("Rear Bumper", {}).get("signs", {}).get("rb-pushed") == {
            "answer": "yes", "photo": f"@{label_id['P03']}", "zone": "zone:rear"},
            "the walk kept on the AI draft, its photo by id", walk)
        items = t.links_of(state["data"].get("second_look") or {})
        by = {it["part"]: it for it in items}
        check(items and all(isinstance(it.get("assembly"), str) and it.get("assembly_name") for it in items),
              "every linked part grouped: an assembly and its name (K1.0's facts)",
              [it["part"] for it in items if not it.get("assembly")])
        beam = by.get("Rear Bumper Beam") or {}
        check(beam.get("bucket") == "likely" and not beam.get("folded")
              and [s["id"] for s in beam.get("signs_yes") or []] == ["rb-pushed"]
              and beam["signs_yes"][0]["photo"] == f"@{label_id['P03']}",
              "a yes sign pointing at the beam lifts it from strip-down to likely, its sign and photo kept", beam)
        hop2 = [it for it in items if it.get("hop") == 2]
        under = by.get("Underbody / Chassis") or {}
        check(len(hop2) >= 1 and under.get("hop") == 2 and under.get("via") == "Rear Bumper Beam"
              and under.get("linked_from") == ["Rear Bumper"]
              and under.get("link_rule") == "K1.0:rear-bumper>beam>underbody-chassis"
              and [s["id"] for s in under.get("signs_yes") or []] == ["rb-corner"],
              "the corner sign opens the beam's chain: Underbody / Chassis, hop 2 via the beam", under)
        check(all(it.get("folded") is True and it.get("bucket") == "strip_down" for it in hop2)
              and under.get("assembly") == "rear-bumper" and under.get("assembly_name") == "Rear bumper assembly"
              and under.get("prior") is None,
              "a second hop sits in the folded strip-down block, grouped with its anchor's assembly, no prior",
              [(it["part"], it.get("folded"), it.get("assembly")) for it in hop2])
        check(len(hop2) <= assembly_walk.MAX_HOP2, f"at most {assembly_walk.MAX_HOP2} second hops a case")
        # No side from the walk: nothing firm and sided is listed at the rear
        # end (the door is at neither), so every sided part off the bumper is
        # undetermined, whatever the walk said about the corner.
        sided = [it for it in items if "Rear Bumper" in it["linked_from"] and it["part"].endswith(UNDETERMINED_SUFFIX)]
        firm_sided = [it for it in items if "Rear Bumper" in it["linked_from"] and split_side(it["part"])[1]]
        check(sided and not firm_sided and not any(it.get("side_from") for it in sided),
              "no side from the walk: the bumper's sided parts stay '(side undetermined)'",
              [it["part"] for it in firm_sided])
        centre = [it["part"] for it in items if it["part"] in ("Rear Number Plate", "Reversing Camera")
                  and "Rear Bumper" in it["linked_from"]]
        check(not centre, "a corner hit: the bumper's centre relations (plate, camera) gated out", centre)
        # Caps.
        shown = [it for it in items if not it.get("folded")]
        folded = [it for it in items if it.get("folded")]
        slots: dict = {}
        for it in items:
            slots[_slot(it)] = slots.get(_slot(it), 0) + 1
        check(len(shown) <= settings.intake_links_max and len(folded) <= settings.intake_links_stripdown_max
              and max(slots.values()) <= settings.intake_links_assembly_max
              and all(it["bucket"] != "strip_down" for it in shown)
              and items == shown + folded,
              "caps: seen + likely within the 12, the block after them within its own 12, at most 4 a group and "
              "bucket, no strip-down part outside the block", slots)
        order_ok = True
        for group in {links.group_of(it) for it in shown}:
            for bucket in links.BUCKETS:
                ranks = [it.get("prior") or links.PRIOR_NONE for it in shown
                         if links.group_of(it) == group and it["bucket"] == bucket and not it.get("intact_in")]
                order_ok &= ranks == sorted(ranks)
        check(order_ok, "prior order: inside each group and bucket the likelier relation first")
        check(all(SecondLookItem.model_validate(it).model_dump() == it for it in items),
              "the stored items are what SecondLookItem keeps (nothing lost on a re-save)")

        # A hand-add on the wizard re-links with the stored walk.
        data = state["data"]
        look = data["second_look"]
        tail = next((it for it in look["items"] if it["part"] == "Left Tail Lamp"), None)
        check(tail is not None and tail.get("source") == "zone", "the left tail lamp: a zone read's suggestion")
        with Settings(intake_assembly_walk="off"):
            no_walk = suggestions.relinked({**suggestions.item_from_suggestion(tail)}, data, look,
                                           session.ai_draft)
        r = client.post(f"/intake/{rid}/suggestions", json={"key": "Left Tail Lamp", "action": "add"})
        check(r.status_code == 200, "added on the wizard", r.text[:300])
        after = {it["part"]: it for it in t.links_of(r.json()["second_look"])}
        quarter = after.get("Left Rear Quarter Panel") or {}
        check(quarter.get("linked_from") == ["Left Tail Lamp"] and quarter.get("hit") in ("heavy", "crush")
              and [s["id"] for s in quarter.get("signs_yes") or []] == ["tl-pushed"]
              and quarter["signs_yes"][0]["photo"] == f"@{label_id['P04']}" and quarter.get("side_from") == "Left Tail Lamp",
              "its re-link reads the walk: the lamp pushed in lifts its hit past medium (heavy, then the rear end's "
              "crush), the quarter panel follows (its side from the listed lamp, never the walk)", quarter)
        check("Left Rear Quarter Panel" not in [it["part"] for it in no_walk],
              "the same re-link without the walk: no quarter panel (the cracked lamp alone is a medium hit)")

        # Filing relabels the walk and the signs' photos.
        state = client.get(f"/intake/{rid}").json()
        r = client.post(f"/intake/{rid}/finalize", json={"data": state["data"]})
        check(r.status_code == 200, "filed", r.text[:300])
        filed = t.db_row(Report, rid)
        fwalk = (filed.ai_draft or {}).get("assembly_walk") or {}
        check(fwalk.get("Rear Bumper", {}).get("signs", {}).get("rb-pushed", {}).get("photo") == "P03"
              and fwalk.get("Left Tail Lamp", {}).get("signs", {}).get("tl-pushed", {}).get("photo") == "P04"
              and fwalk["Rear Bumper"]["signs"]["rb-lid-gap"]["photo"] is None,
              "filing: the walk's photos as the case's P-numbers", fwalk)
        fitems = {it["part"]: it for it in t.links_of(filed.data.get("second_look") or {})}
        check((fitems.get("Rear Bumper Beam") or {}).get("signs_yes", [{}])[0].get("photo") == "P03"
              and (fitems.get("Left Rear Quarter Panel") or {}).get("signs_yes", [{}])[0].get("photo") == "P04",
              "filing: each yes sign's photo on the linked parts as a P-number")

        # The case page: undo the add, add again -- the re-link reads the
        # filed draft's walk.
        r = client.post(f"/reports/{rid}/suggestions", json={"key": "Left Tail Lamp", "action": "undo"})
        gone = "Left Rear Quarter Panel" not in [it["part"] for it in t.links_of(r.json().get("second_look") or {})]
        check(r.status_code == 200 and gone, "case page: an undo takes the lamp's re-linked part back", r.text[:300])
        r = client.post(f"/reports/{rid}/suggestions", json={"key": "Left Tail Lamp", "action": "add"})
        again = {it["part"]: it for it in t.links_of(r.json().get("second_look") or {})}
        q2 = again.get("Left Rear Quarter Panel") or {}
        check(r.status_code == 200 and [s["id"] for s in q2.get("signs_yes") or []] == ["tl-pushed"]
              and q2["signs_yes"][0]["photo"] == "P04",
              "case page: adding it again re-links with the filed walk (its photo a P-number)", q2)

        # Sign-off: the AI-accuracy record counts the car map's groups.
        check(t.sign_off(client, rid).status_code == 200, "signed off")
        row = t.db_row(AiAccuracy, rid)
        s = (row.metrics or {}).get("suggestions") if row else {}
        check(row is not None and row.version == 3 and "rear-bumper" in (s.get("by_assembly") or {})
              and (s.get("by_hop") or {}).get("2", {}).get("shown", 0) >= 1
              and (s.get("folded") or {}).get("shown", 0) >= 1 and (s.get("sign_lifted") or {}).get("shown", 0) >= 2
              and s.get("links_table") == "K1.0",
              "the AI-accuracy row (still VERSION 3): by assembly, by hop, the folded block, the sign-lifted parts, "
              "table K1.0", {k: s.get(k) for k in ("by_hop", "folded", "sign_lifted", "links_table")} if s else row)


# ------------------------------------------------------------- 6. the replay

REPLAY_KEY = {"cases": [{"name": "TEST CASE R", "folder": "case_r", "parts": [
    {"part": "Rear Bumper"}, {"part": "Rear Bumper Beam", "hidden": True}, {"part": "Underbody / Chassis"}]}]}


def _replay_run(with_walk: bool) -> dict:
    run = {"case": "TEST CASE R", "rep": 1, "parts": {"found": 1}, "drafted_parts": ["Rear Bumper"],
           "drafted_items": [{"part": "Rear Bumper", "condition_tokens": ["Crumpled"], "severity": "Severe",
                              "ai_confidence": "High", "found_by": ["overall", "zone:rear"], "seen_in": ["P01"],
                              "photo_reference": "P01"}],
           "suggestions": [],
           "sorted_photos": [{"n": 1, "kind": "damage", "area": "rear", "quality": "good", "damage": True,
                              "part": "Rear Bumper"}],
           "zones": {"status": "ok", "zones": [{"zone": "rear", "status": "ok", "photos": ["P01"], "intact": []}]}}
    if with_walk:
        run["assembly_walk"] = {"Rear Bumper": {"hit_at": "corner", "signs": {
            "rb-pushed": {"answer": "yes", "photo": "P01", "zone": "zone:rear"},
            "rb-corner": {"answer": "yes", "photo": "P01", "zone": "zone:rear"}}}}
    return run


def replay_section() -> None:
    work = Path(tempfile.mkdtemp(prefix="carmap-int-replay-"))
    walked, plain, key = work / "walked.json", work / "plain.json", work / "key.json"
    walked.write_text(json.dumps({"label": "walked", "ai_settings": {"intake_walk_raise": "one", "intake_link_hops": 2,
                                                                     "car_map_version": "K1.0"},
                                  "runs": [_replay_run(True)]}), encoding="utf-8")
    plain.write_text(json.dumps({"label": "plain", "runs": [_replay_run(False)]}), encoding="utf-8")
    key.write_text(json.dumps(REPLAY_KEY), encoding="utf-8")

    def replay(src: Path, *flags) -> tuple[int, dict, str]:
        out = work / f"out-{len(list(work.iterdir()))}.json"
        proc = subprocess.run([PY, str(HERE / "bench" / "replay_links.py"), str(src), "--key", str(key), "--out",
                               str(out), *flags], capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
                              env=clean_env(), timeout=600)
        doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        return proc.returncode, doc, proc.stdout + proc.stderr

    cm = str(ROOT / "app" / "intake" / "car_map.json")
    rc, doc, log = replay(walked, "--car-map", cm, "--stripdown-cap", "out")
    rows = {r["part"]: r for r in (doc.get("runs") or [{}])[0].get("link_suggestions") or []}
    under, beam = rows.get("Underbody / Chassis") or {}, rows.get("Rear Bumper Beam") or {}
    check(rc == 0 and under.get("hop") == 2 and under.get("via") == "Rear Bumper Beam" and under.get("folded")
          and [s["id"] for s in beam.get("signs_yes") or []] == ["rb-pushed"] and beam.get("bucket") == "likely",
          "a stored walk is replayed (hops 2 as the file recorded): the beam lifted, the second hop folded",
          log[-800:])
    w = doc.get("walk") or {}
    check(rc == 0 and (doc.get("review_screen") or {}).get("map_frozen") is True and "the frozen file" in log,
          "the replay says the map is K1.0's frozen file (K1-freeze.md)", doc.get("review_screen"))
    check(w.get("version") == "K1.0" and w.get("runs_with_walk") == 1 and w.get("summary", {}).get("hop2") == 1
          and (doc["runs"][0].get("walk") or {}).get("used") is True and "assembly walk:" in log,
          "the replay records the walk it read (map, runs, second hops) and says so", w)
    rc, doc, _ = replay(walked, "--car-map", cm, "--stripdown-cap", "out", "--no-walk")
    rows = {r["part"]: r for r in (doc.get("runs") or [{}])[0].get("link_suggestions") or []}
    check(rc == 0 and "Underbody / Chassis" not in rows and (rows.get("Rear Bumper Beam") or {}).get("bucket") == "strip_down"
          and "walk" not in doc and not any("signs_yes" in r or r.get("hop") == 2 for r in rows.values()),
          "--no-walk: the same reads without the walk (the baseline)")
    rc, doc, _ = replay(walked, "--car-map", cm, "--stripdown-cap", "out", "--link-hops", "1")
    rows = {r["part"]: r for r in (doc.get("runs") or [{}])[0].get("link_suggestions") or []}
    check(rc == 0 and "Underbody / Chassis" not in rows and rows.get("Rear Bumper Beam", {}).get("signs_yes"),
          "--link-hops 1 over the file's 2: the lift stays, no second hop")
    rc, doc, _ = replay(walked)
    rows = {r["part"]: r for r in (doc.get("runs") or [{}])[0].get("link_suggestions") or []}
    check(rc == 0 and "Underbody / Chassis" not in rows and doc.get("walk", {}).get("map") == "app/intake/car_map.json"
          and (rows.get("Rear Bumper Beam") or {}).get("bucket") == "strip_down",
          "on L1 (no --car-map): the walk read against the app's map, only the hit raise can act")
    rc_plain, doc_plain, _ = replay(plain)
    check(rc_plain == 0 and "walk" not in doc_plain and not any(
        k in r for r in doc_plain["runs"][0]["link_suggestions"] for k in ("hop", "via", "signs_yes")),
          "a file without a walk: as before (no walk block, no walk fields)")
    rc_bad, _, log = replay(plain, "--walk-raise", "full")
    check(rc_bad == 2 and "no stored run keeps an assembly walk" in log, "a walk flag with no stored walk: exit 2")


# ------------------------------------------------------------- 7. the benchmark


def bench_section() -> None:
    import benchmark_intake as bi

    defaults = vars(bi.parse_args([]))
    check({k: defaults[k] for k in bi.CARMAP_ARGS} == bi.CARMAP_ARGS and bi.model_settings(bi.parse_args([])) == {},
          "the car map flags' defaults are CARMAP_ARGS (left out of args); none given, no setting")
    got = bi.model_settings(bi.parse_args(["--read-mode", "zones", "--assembly-walk", "--links", "shadow",
                                           "--walk-raise", "one", "--link-hops", "2", "--car-map", "ON",
                                           "--links-order", "Prior", "--assembly-max", "4", "--stripdown-cap", "out",
                                           "--stripdown-max", "12"]))
    check({k: got.get(k) for k in ("intake_car_map", "intake_links_order", "intake_links_assembly_max",
                                   "intake_links_stripdown_cap", "intake_links_stripdown_max", "intake_link_hops")}
          == {"intake_car_map": "on", "intake_links_order": "prior", "intake_links_assembly_max": 4,
              "intake_links_stripdown_cap": "out", "intake_links_stripdown_max": 12, "intake_link_hops": 2},
          "the full arm's flags: each its setting", got)
    got = bi.model_settings(bi.parse_args(["--links", "on", "--stripdown-cap", "out"]))
    check(got == {"intake_links": "on", "intake_links_stripdown_cap": "out"}, "strip-down out on L1 alone", got)
    for bad, says in ((["--car-map", "on"], "--links on"), (["--stripdown-cap", "out"], "--links on"),
                      (["--links", "on", "--links-order", "prior"], "--car-map"),
                      (["--links", "on", "--stripdown-max", "6"], "--stripdown-cap out"),
                      (["--links", "on", "--assembly-max", "-1"], "--assembly-max"),
                      (["--links", "on", "--stripdown-cap", "half"], "--stripdown-cap"),
                      (["--links", "on", "--car-map", " "], "--car-map")):
        try:
            bi.model_settings(bi.parse_args(bad))
            check(False, f"{bad} should stop the run")
        except SystemExit as exc:
            check(says in str(exc.code), f"stops the run: {' '.join(bad)}", exc.code)
    real_map = car_map.MAP
    fake = types.SimpleNamespace(intake_car_map="off")
    try:
        bi.use_car_map("on", fake)
        with Settings(intake_links="on", intake_car_map="on"):
            rec = bi.ai_settings(settings)
        check(fake.intake_car_map == "on" and car_map.MAP is real_map
              and bi.CAR_MAP_USED == {"source": "app/intake/car_map.json", "sha256": FROZEN_K1}
              and rec.get("car_map_frozen") is True and rec.get("car_map_version") == "K1.0",
              "--car-map on: the app's K1.0, its sha recorded, the frozen file")
        check(car_map.frozen_as("K1.0", "0" * 64) is False and car_map.frozen_as("L1", None) is None,
              "a changed file under a frozen version: not frozen; a version with no freeze line: unknown")
        work = Path(tempfile.mkdtemp(prefix="carmap-int-bench-"))
        other = copy.deepcopy(real_map)
        other["version"] = "K1.0-test"
        path = work / "map.json"
        path.write_text(json.dumps(other), encoding="utf-8")
        bi.use_car_map(str(path), fake)
        with Settings(intake_links="on", intake_car_map="on", intake_read_mode="zones", intake_assembly_walk="on"):
            rec = bi.ai_settings(settings)
        check(car_map.MAP.get("version") == "K1.0-test" and rec.get("car_map_version") == "K1.0-test"
              and rec.get("car_map_source") == str(path) and rec.get("car_map_sha256") == car_map.sha256_lf(path)
              and rec.get("intake_car_map") == "on" and rec.get("intake_assembly_walk") == "on"
              and rec.get("car_map_frozen") is None,
              "--car-map PATH: that map in place for the links and the walk; ai_settings names it (source, version, "
              "sha256)", {k: rec.get(k) for k in ("car_map_version", "car_map_source", "car_map_sha256")})
        bi.use_car_map("from-l1", fake)
        check(car_map.MAP.get("version") == "L1" and bi.CAR_MAP_USED.get("sha256") is None,
              "--car-map from-l1: L1 converted, no file sha")
        bad = work / "bad.json"
        bad.write_text("{nope", encoding="utf-8")
        try:
            bi.use_car_map(str(bad), fake)
            check(False, "a map that doesn't load should stop the run")
        except SystemExit as exc:
            check("no car map loaded" in str(exc.code), "a map that doesn't load stops the run", exc.code)
        bi.use_car_map("off", fake)
        check(fake.intake_car_map == "off", "--car-map off: L1")
    finally:
        car_map.MAP = real_map
        bi.CAR_MAP_USED.clear()
    with Settings(intake_links="on"):
        plain = bi.ai_settings(settings)
    check(not any(k in plain for k in ("intake_car_map", "car_map_version", "car_map_source", "intake_links_order",
                                       "intake_assembly_walk")),
          "ai_settings at today's settings: nothing of the car map (a run reads as before)")
    with Settings(intake_links="on", intake_car_map="on", intake_links_stripdown_cap="out"):
        check(bi.review_on(settings) and bi.links_table_shown(settings).get("version") == "K1.0",
              "with the map on: link rows keep the group fields, inside parts from the map's table")
    with Settings(intake_links="on"):
        check(not bi.review_on(settings) and bi.links_table_shown(settings) is links.TABLE,
              "at the defaults: neither (rows and inside parts as before)")


# ------------------------------------------------------------- 8. AI accuracy


def accuracy_section() -> None:
    final = {"damage_summary": [{"part": "Rear Bumper"}, {"part": "Rear Bumper Beam", "added_from": "suggestion"}]}
    plain_look = {"items": [{"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "source": "link",
                             "link_rule": "L1:rear-bumper>beam", "link_type": "behind", "bucket": "strip_down",
                             "hit": "heavy"}], "dismissed": [], "decisions": []}
    old = accuracy.compare({}, {**final, "second_look": plain_look})
    s = old["suggestions"]
    check(not any(k in s for k in (*accuracy.CARMAP_GROUPS, *accuracy.CARMAP_TALLIES, "car_map_shadow")),
          "an L1 case: none of the car map's keys (its row as before)")
    look = copy.deepcopy(plain_look)
    look["items"][0].update(link_rule="K1.0:rear-bumper>beam", assembly="rear-bumper", hop=1, signs_yes=[
        {"id": "rb-pushed", "ask": "?", "photo": "P03"}], bucket="likely")
    look["items"].append({"key": "Underbody / Chassis", "part": "Underbody / Chassis", "source": "link",
                          "link_rule": "K1.0:rear-bumper>beam>underbody-chassis", "link_type": "crush",
                          "bucket": "strip_down", "hit": "crush", "assembly": "rear-bumper", "hop": 2, "folded": True,
                          "via": "Rear Bumper Beam", "signs_yes": [{"id": "rb-corner", "ask": "?", "photo": "P03"}]})
    look["items"].append({"key": "Left Front Door Hinge", "part": "Left Front Door Hinge", "source": "link",
                          "link_rule": "K1.0:front-door>hinge", "assembly": "front-door.left", "hop": 1})
    new = accuracy.compare({"car_map_suggestions": [{"key": "Rear Bumper Beam", "part": "Rear Bumper Beam",
                                                     "link_rule": "K1.0:rear-bumper>beam"}]},
                           {**final, "second_look": look})
    s = new["suggestions"]
    check(s["by_assembly"] == {"rear-bumper": {"shown": 2, "added": 1, "dismissed": 0, "left": 1},
                               "front-door": {"shown": 1, "added": 0, "dismissed": 0, "left": 1}}
          and s["by_hop"]["2"] == {"shown": 1, "added": 0, "dismissed": 0, "left": 1}
          and s["by_hop"]["1"]["shown"] == 2
          and s["folded"] == {"shown": 1, "added": 0, "dismissed": 0, "left": 1}
          and s["sign_lifted"] == {"shown": 2, "added": 1, "dismissed": 0, "left": 1}
          and s["car_map_shadow"]["predicted"] == 1 and s["car_map_shadow"]["on_final_list"] == 1
          and s["links_table"] == "K1.0" and new["version"] == accuracy.VERSION == 3,
          "a car map case: by assembly (the side cut off), by hop, folded, sign-lifted, the car map's shadow; "
          "VERSION 3", {k: s.get(k) for k in ("by_assembly", "by_hop", "folded", "sign_lifted", "car_map_shadow")})
    before = accuracy.sum_suggestions([old])
    check(not any(k in before for k in (*accuracy.CARMAP_GROUPS, *accuracy.CARMAP_TALLIES, "car_map_shadow")),
          "the sum of L1 rows: as before (no car map key)")
    both = accuracy.sum_suggestions([old, new, {"suggestions": None}, {}])
    check(both["by_hop"]["2"]["shown"] == 1 and both["folded"]["shown"] == 1 and both["sign_lifted"]["shown"] == 2
          and both["by_assembly"]["rear-bumper"]["shown"] == 2 and both["car_map_shadow"]["predicted"] == 1
          and both["shown"] == old["suggestions"]["shown"] + new["suggestions"]["shown"]
          and both["links_table"] == ["K1.0", "L1"],
          "the sum with a car map row: its counts added, the old ones as before")


# ------------------------------------------------------------- 9. the seams between K3b and K4


def _walk(answers: dict, hops: int = 2) -> "assembly_walk.Walk":
    return assembly_walk.for_links(answers, assembly_walk.index_for(car_map.MAP), "one", hops)


def _rec(hit_at: str, **signs) -> dict:
    return {"hit_at": hit_at, "signs": {sid.replace("_", "-"): {"answer": a, "photo": p} for sid, (a, p) in signs.items()}}


def seams_section() -> None:
    cm = car_map.MAP
    look = car_map.review_lookup(cm)
    info = look("K1.0:rear-bumper>beam>underbody-chassis") or {}
    check(info.get("assembly") == "rear-bumper" and info.get("hop") == 2 and info.get("prior") is None
          and info.get("sided") is False and info.get("assembly_name") == "Rear bumper assembly",
          "review_lookup knows a second hop's rule: its chain's first relation's group, hop 2, no prior", info)
    door = look("K1.0:front-door>sill-rocker-panel>underbody-chassis") or {}
    check(door.get("assembly") == "front-door" and door.get("sided") is True,
          "a chain from a sided assembly is sided (one group per door)", door)
    sub = look("K1.0:rear-quarter-panel>suspension-rear>rear-subframe") or {}
    check(sub.get("fitted") == "if_fitted", "'if fitted' from the chain's end part (the rear subframe)", sub)
    check(car_map.relation_info(cm, "K1.0:rear-bumper>beam>underbody-chassis") is None
          and look("L1:rear-bumper>beam") is None and look("K1.0:no>such>chain") is None,
          "relation_info as it was (a chain is no relation); another table's or an unknown rule: nothing")

    table = car_map.links_table(cm)
    bumper = [t_item("Rear Bumper", "Crumpled", severity="Severe")]
    walk = _walk({"Rear Bumper": _rec("corner", rb_pushed=("yes", "P01"), rb_corner=("yes", "P01"))})
    roomy = dict(mode="on", stripdown_cap="out", assembly_max=20, max_items=40, stripdown_max=40)
    for order in ("table", "prior"):
        found = links.build(copy.deepcopy(bumper), [], [], {}, table, links.Options(**roomy, order=order), info=look,
                            walk=walk)
        block = [it for it in found if it.get("folded")]
        check(block and block[-1]["part"] == "Underbody / Chassis" and block[-1]["hop"] == 2
              and all(it.get("hop") != 2 for it in block[:-1]),
              f"order {order}: the second hop after every first hop of its bucket"
              + (" (past the rank-5 ones)" if order == "prior" else ""), [(it["part"], it.get("prior")) for it in block])
    ranks = [it.get("prior") or links.PRIOR_NONE for it in block[:-1]]
    check(ranks == sorted(ranks) and max(ranks) == 5, "... and the first hops in prior order up to rank 5", ranks)

    group = [{"key": p, "part": p, "bucket": "strip_down", "linked_from": ["Rear Bumper"], "assembly": "rear-bumper"}
             for p in "ABCDE"]
    hop2 = {"key": "H", "part": "H", "bucket": "strip_down", "hop": 2, "linked_from": ["Rear Bumper"],
            "assembly": "rear-bumper"}
    out = links._cap(copy.deepcopy(group + [hop2]), links.Options(mode="on", stripdown_cap="out"))
    check([it["part"] for it in out] == ["A", "B", "C", "D", "H"] and all(it.get("folded") for it in out),
          "the per-group cap: a second hop has its own slot, never crowded out of the block by its group's first "
          "hops (the group's fifth strip-down part still is)", [it["part"] for it in out])
    out = links._cap(copy.deepcopy(group + [hop2]), links.Options(mode="on"))
    check([it["part"] for it in out] == ["A", "B", "C", "D", "H"] and not any(it.get("folded") for it in out),
          "... with strip-down in the 12 too (still within intake_links_max)")
    found = links.build(copy.deepcopy(bumper), [], [], {}, table, links.Options(mode="on", stripdown_cap="out"),
                        info=look, walk=walk)
    check(any(it["part"] == "Underbody / Chassis" and it.get("folded") for it in found)
          and max(sum(1 for it in found if links.cap_slot(it) == s) for s in {links.cap_slot(it) for it in found}) <= 4,
          "today's caps (4 a group, 12, 12): the second hop shows on a heavy rear hit", [it["part"] for it in found])

    door = [t_item("Left Front Door", "Buckled")]
    walk = _walk({"Left Front Door": _rec("whole", fd_low=("yes", "P02"))})
    found = links.build(copy.deepcopy(door), [], [], {}, table, links.Options(mode="on", stripdown_cap="out"),
                        info=look, walk=walk)
    under = next((it for it in found if it["part"] == "Underbody / Chassis"), {})
    sill = next((it for it in found if it["part"] == "Left Sill / Rocker Panel"), {})
    check(under.get("hop") == 2 and under.get("via") == "Left Sill / Rocker Panel"
          and under.get("assembly") == "front-door.left" and sill.get("assembly") == "front-door.left"
          and sill.get("side_from") == "Left Front Door",
          "a door's chain: the second hop in the left door's group, the sill's side from the listed door", under)
    walk_off = links.build(copy.deepcopy(door), [], [], {}, table, links.Options(mode="on", stripdown_cap="out"),
                           info=look)
    check(not any(it.get("hop") == 2 for it in walk_off), "the same without the walk: no second hop")


# ------------------------------------------------------------- main


def main() -> int:
    t.init_db()
    check(L1 is not None and L1.get("version") == "L1", "the L1 table loads")
    for name, fn in (
        ("1. settings: defaults, compose", settings_section),
        ("2. everything off: the requests byte for byte master's (B1 snapshots)", requests_section),
        ("3. everything off: the linked parts through the seam == master's links.py", links_section),
        ("4. everything off, end to end: master 99aaa85's requests and stored data", end_to_end_off),
        ("5. everything on, end to end: K1.0, walk, hops 2, strip-down out, prior", end_to_end_on),
        ("6. the replay reads a stored walk", replay_section),
        ("7. the benchmark's car map flags", bench_section),
        ("8. the AI-accuracy record", accuracy_section),
        ("9. the seams between the walk (K3b) and the review screen (K4)", seams_section),
    ):
        run_section(name, fn)
    print(f"\ntest_carmap_int: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
