"""OFFLINE, 0 Gemini calls: the linked parts (contract 10, app/intake/
links.py) replayed on stored benchmark runs, and scored against the key.

    python scripts/bench/replay_links.py RESULT.json [MORE.json ...] --key KEY.json [--out OUT.json]
        [--variant NAME ...] [--label L] [--ref R] [--freeze L1-freeze.md] [--not-frozen-ok]
        [--car-map PATH|from-l1|off] [--links-order table|prior] [--assembly-max N]
        [--stripdown-cap in|out] [--stripdown-max N]
        [--no-walk] [--walk-raise one|full] [--link-hops 1|2]

For every stored run that scored (benchmark_intake.py --out; an errored run
-- a 503 storm, no list -- is skipped, never scored as a miss), the inputs
are rebuilt as second_look._store hands them to links.build_all:

  - the list: the run's drafted_items (condition tokens, severity, remarks,
    damage type, side_inferred, ai_confidence, found_by, seen_in -- files
    from before found_by or evidence work too, their parts are just not
    "firm" under the strict side);
  - the other suggestions: the run's stored suggestions (contract 9 files),
    else second_look.suggested's names (older files), else none;
  - the photos: sorted_photos, the sorter's tags as stored (the "seen"
    bucket);
  - what each zone read that answered called intact: zones[].intact, as
    ai_draft["zone_intact"] = {"zone:rear": [...]};
  - an "impact" link takes its side from the firm sided parts listed at
    that end, else "(side undetermined)" -- never from the point of impact
    (the AI's own draft), as _store builds it;

then links.build_all with the frozen L1 table and the knobs frozen with it
(L1-freeze.md), exactly as _store calls it, and the same filter after it
(a link on the list or dismissed goes). Each link is scored with the
benchmark's own strict scorer (benchmark_intake.score_links: real = a key
part the list AND the other suggestions missed, classify_suggestions
against them; wrong_side; extra), with the recall of list + suggestions +
links of all the key's parts and of its inside ones (the key's hidden parts
and the table's hidden children), by kind of link, bucket and hit level --
per case and per 4-case run (each stored file's repetitions, numbered on
across the files given: a group such as lite1 = two files). The damage
list must come out byte for byte as it went in: asserted on every run (exit
3 otherwise).

--variant runs the pre-registered variants as well, for sensitivity only
(VARIANTS: one knob each -- the strict side, unknown hit light, intact
dropped, no crush links); the frozen defaults are always run and are the
headline. Nothing here may be tuned on these numbers (contract10.md 2).

The guard: importing this file refuses any import of the Gemini client
(google.genai, app.ai.client / structured / keys / decisions) or of an HTTP
library, and any network connection -- the replay can make no call (and
exits 4 should one be loaded all the same). The
table: its version and sha256 (line endings as git keeps them) and the
skill files' are checked against the L1 freeze (FROZEN, or --freeze FILE);
a mismatch stops the replay (exit 2) unless --not-frozen-ok, which labels
every number "NOT the frozen table". The knob defaults in app/config.py are
checked the same way.

The car map's review screen (K4, plans/2026-11-18-car-knowledge/
K1-design.md 5b; still 0 calls): --car-map builds from a car map's table
(car_map.links_table; no body gate -- a stored run keeps no make and
model) with its facts (car_map.review_lookup: groups by assembly, priors,
"also check" words); "from-l1" is L1 converted (car_map.from_l1, the same
links grouped one assembly per L1 parent); a map that doesn't load stops
the replay (exit 2). --links-order, --assembly-max, --stripdown-cap and
--stripdown-max set links.Options' K4 knobs, on L1 as on a map. Any of
them (even at its default, "--car-map off" too) adds the review metrics
per case-run: the strip-down block's size (folded) and the strip-down
parts shown, the cap hits (what intake_links_max cut, seen + likely and
strip-down, beside an uncapped build; what the per-group cap cut beside
one without it), the items per group (assembly, else listed part); and
the K4 fields on each row. With none of them the output is today's, byte
for byte.

The assembly walk (K3b, K1-design.md 5a; still 0 calls): a run stored with
its walk (benchmark_intake.py --assembly-walk keeps "assembly_walk", photos
by label) is replayed with it, as second_look._store builds it
(assembly_walk.for_links: the yes signs raise a hit, gate positions, lift a
strip-down part, and with hops 2 open a chain's second hop) -- with the
raise and hops the file recorded (ai_settings intake_walk_raise /
intake_link_hops; else one / 1), or --walk-raise / --link-hops; --no-walk
replays the same reads without it (the baseline). The signs are read
against the --car-map map when it has signs, else the app's car map
(app/intake/car_map.json); positions, lifts and chains act only on that
map's own table (--car-map), the hit raise on L1 too. Then each row keeps
hop / via / signs_yes and each case-run a "walk" block; a walk flag with
no stored walk in the files, or no map with signs, stops the replay (exit
2). Files without a walk replay as before, byte for byte.

Prints per variant the 4-case-run totals, a per-case table and the kinds /
buckets / hits; --out writes every case-run's links (part names, kinds,
buckets, truth -- no photo, no plate beyond the stored file's case names)
with the summary benchmark_intake.summarize makes of them, so compare.py
and gate.py --suggestions read it like a result file. Deterministic: the
same files give the same output, byte for byte.
"""
import argparse
import copy
import importlib.abc
import json
import re
import socket
import statistics
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = SCRIPTS_DIR.parent
for _path in (str(BOT_DIR), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ---------------------------------------------------------------- the guard

# Modules the replay may never load: the Gemini client and what calls it,
# and the HTTP libraries. A name or anything under it.
BLOCKED_MODULES = (
    "google.genai", "google.generativeai", "google.ai", "app.ai.client", "app.ai.structured", "app.ai.keys",
    "app.ai.decisions", "httpx", "requests", "urllib3", "aiohttp", "grpc", "websockets",
)


class GuardError(RuntimeError):
    """The replay tried to do what it never may: call Gemini or the web."""


def blocked(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BLOCKED_MODULES)


class _NoGemini(importlib.abc.MetaPathFinder):
    """Refuses to import a BLOCKED_MODULES module, wherever it is asked for."""

    def find_spec(self, name, path=None, target=None):
        if blocked(name):
            raise ImportError(f"replay_links: importing {name} is refused -- the replay makes no Gemini call and "
                              f"no web request")
        return None


def _no_network(*args, **kwargs):
    raise GuardError("replay_links: a network connection was attempted -- the replay is offline")


def install_guard() -> dict:
    """The guard on (once): blocked imports refused, socket connections and
    name look-ups raise GuardError. Refuses to start (GuardError) when a
    blocked module is loaded already."""
    loaded = sorted(m for m in sys.modules if blocked(m))
    if loaded:
        raise GuardError(f"replay_links: {', '.join(loaded)} already imported; run the replay on its own")
    if not any(isinstance(f, _NoGemini) for f in sys.meta_path):
        sys.meta_path.insert(0, _NoGemini())
    socket.socket.connect = _no_network
    socket.socket.connect_ex = _no_network
    socket.create_connection = _no_network
    socket.getaddrinfo = _no_network
    return {"blocked_imports": list(BLOCKED_MODULES), "network": "refused"}


GUARD = install_guard()

# Only now the app: links and what it needs (config, taxonomy, evidence,
# prefill's item_key) -- none of them a client.
import benchmark_intake as bench  # noqa: E402
from app import config  # noqa: E402
from app.intake import links  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402

# ---------------------------------------------------------------- the freeze (L1-freeze.md)

FROZEN_VERSION = "L1"
# sha256 of git's copy (LF) of each frozen file, under apps/bot-service/.
FROZEN_SHA256 = {
    "app/intake/link_table.json": "06bbebfaf5bcb0b2561ba3b3fcec2f2f7c79d2f66b8a2ce7b22cd4003fe767f3",
    "app/ai/skills/front.md": "494ce5423b295983010e536c536782395a325496e5c674e33a5d2173b3abc555",
    "app/ai/skills/rear.md": "5fefdb7247a84ae72ff228a9a068b760f79b53621d89c8bd3f599c2dfd9e757a",
    "app/ai/skills/side.md": "f5c470b4e89370147ad9c62a4e322cb46a5667be037ade0b0ad3f3c3755e172f",
    "app/ai/skills/underbody.md": "ac05a8c64ddb9ecb0fa84f52e79e659e217dd7f509159d0bda084a557b5c13ef",
    "app/ai/skills/wheel.md": "4cc72a166357017491f54aaf17fcad8bc7fa931181b042ba0ae826d35039606f",
}
# The knob defaults frozen with the table (contract10.md 4.6).
FROZEN_KNOBS = {
    "intake_links_max": 12,
    "intake_links_types": "attached,opens_with,behind,crush",
    "intake_links_side": "firm",
    "intake_links_unknown_hit": "medium",
    "intake_links_intact": "demote",
    "intake_links_catalogue": False,
    "intake_link_check_max_calls": 2,
}
_FREEZE_LINE = re.compile(r"^([0-9a-f]{64}) \*?apps/bot-service/(\S+)\s*$")
_FREEZE_VERSION = re.compile(r"^#\s*(\S+)\s+freeze\b", re.IGNORECASE)

# Pre-registered (plan.md 8.1), for sensitivity only: one knob each off its
# frozen default. "default" is the frozen setup, the headline.
VARIANTS = {
    "default": {},
    "side_strict": {"side": "strict"},
    "unknown_light": {"unknown_hit": "light"},
    "intact_drop": {"intact": "drop"},
    "no_crush": {"types": ("attached", "opens_with", "behind")},
}
# What a replayed link keeps (no reason text, no photo of the parent).
ROW_FIELDS = ("key", "part", "link_type", "link_rule", "hit", "bucket", "hidden", "needs_strip_down", "linked_from",
              "side_from", "intact_in", "seen_in")
# ... and with the review knobs (K4), the car map's fields too.
REVIEW_ROW_FIELDS = ("assembly", "assembly_name", "prior", "hop", "fitted", "also_check", "folded")
# links.Options' K4 knobs, by flag.
REVIEW_FLAGS = (("links_order", "order"), ("assembly_max", "assembly_max"), ("stripdown_cap", "stripdown_cap"),
                ("stripdown_max", "stripdown_max"))
# ... and with a stored assembly walk (K3b).
WALK_ROW_FIELDS = ("hop", "via", "signs_yes")
# A cap no case reaches: the builds the cap hits are counted against.
UNCAPPED = 10 ** 6
# links.Options' fields that only a K4 flag shows in the output.
K4_OPTIONS = ("order", "assembly_max", "stripdown_cap", "stripdown_max")


def parse_freeze(text: str) -> tuple[str | None, dict[str, str]]:
    """(version, {path under apps/bot-service: sha256}) from a freeze file
    as L1-freeze.md writes it ("<sha256> *apps/bot-service/<path>")."""
    version, shas = None, {}
    for line in text.splitlines():
        line = line.strip()
        if version is None and (m := _FREEZE_VERSION.match(line)):
            version = m.group(1)
        if m := _FREEZE_LINE.match(line):
            shas[m.group(2)] = m.group(1)
    return version, shas


def check_freeze(version: str | None = None, shas: dict[str, str] | None = None) -> dict:
    """The table and skill files on disk, and the knob defaults in
    app/config.py, against the freeze: {"ok", "version", "frozen_version",
    "files": {path: {sha256, frozen, ok}}, "knobs": {name: {default,
    frozen, ok}}}. sha256 of the file as git keeps it (LF)."""
    version = version or FROZEN_VERSION
    shas = shas or FROZEN_SHA256
    files = {}
    for path, frozen in sorted(shas.items()):
        got = bench.lf_sha256(BOT_DIR / path)
        files[path] = {"sha256": got, "frozen": frozen, "ok": got == frozen}
    knobs = {}
    for name, frozen in FROZEN_KNOBS.items():
        field = config.Settings.model_fields.get(name)
        default = field.default if field is not None else None
        knobs[name] = {"default": default, "frozen": frozen, "ok": default == frozen}
    table_version = links.TABLE_VERSION
    ok = (links.TABLE is not None and table_version == version and all(f["ok"] for f in files.values())
          and all(k["ok"] for k in knobs.values()))
    return {"ok": ok, "version": table_version, "frozen_version": version,
            "table_sha256_raw": links.table_sha(), "files": files, "knobs": knobs}


def frozen_options(**change) -> links.Options:
    """links.Options at the frozen knobs (mode "on": build ignores it), with
    one variant's change."""
    opts = links.Options(mode="on", max_items=FROZEN_KNOBS["intake_links_max"],
                         types=tuple(config.link_types(FROZEN_KNOBS["intake_links_types"])),
                         side=FROZEN_KNOBS["intake_links_side"], unknown_hit=FROZEN_KNOBS["intake_links_unknown_hit"],
                         intact=FROZEN_KNOBS["intake_links_intact"], catalogue=FROZEN_KNOBS["intake_links_catalogue"])
    return links.Options(**{**opts.__dict__, **change})


# ---------------------------------------------------------------- one stored run


def listed_of(run: dict) -> list[dict]:
    """The run's damage list: drafted_items, or (older files) its names."""
    items = run.get("drafted_items")
    if isinstance(items, list) and items:
        return [i for i in items if isinstance(i, dict)]
    return [{"part": p} for p in run.get("drafted_parts") or [] if p]


def suggestions_of(run: dict) -> tuple[list[dict], str]:
    """(the run's other suggestions as _store's open ones, where they came
    from): the stored rows (contract 9 on; never a linked part), else
    second_look.suggested's names, else none. Each has its key."""
    rows = run.get("suggestions")
    if isinstance(rows, list):
        out = []
        for r in rows:
            if not (isinstance(r, dict) and r.get("part")) or r.get("source") == "link":
                continue
            item = {k: copy.deepcopy(v) for k, v in r.items() if k not in ("truth", "check")}
            item["key"] = item.get("key") or item_key(item) or item["part"]
            out.append(item)
        return out, "suggestions"
    names = (run.get("second_look") or {}).get("suggested")
    if isinstance(names, list):
        return [{"part": n, "key": item_key({"part": n}) or n} for n in names if isinstance(n, str) and n], \
            "second_look.suggested"
    return [], "none"


def zone_intact_of(run: dict) -> dict[str, list[str]]:
    """ai_draft["zone_intact"] as the run's zone reads left it: each read
    that answered, {"zone:rear": [part, ...]}; {} in single mode."""
    zones = (run.get("zones") or {}).get("zones") if isinstance(run.get("zones"), dict) else None
    out = {}
    for z in zones or []:
        if isinstance(z, dict) and z.get("status") == "ok" and z.get("zone") and isinstance(z.get("intact"), list):
            out[f"zone:{z['zone']}"] = [p for p in z["intact"] if isinstance(p, str)]
    return out


def _shown(listed, others, photos, intact, table, opts, review, walk=None) -> tuple[list[dict], dict]:
    """build_all as second_look._store calls it ("on": a link on the list
    goes), with the car map's facts and "also check" words when review
    has them, and the run's assembly walk when it has one."""
    info = (review or {}).get("info")
    kw = {"info": info} if info is not None else {}
    walk_kw = {"walk": walk} if walk is not None else {}
    items, joins = links.build_all(listed, others, photos, intact, table, opts, dismissed=(), **kw, **walk_kw)
    names = {str(i.get("part")) for i in listed}
    items = [it for it in items if not set(links.covers(it["part"])) & names]
    if info is not None:
        review["module"].add_also_check(items, review["map"], links.levels_of(listed, photos, opts, **walk_kw))
    return items, joins


def walk_of(run: dict, walking: dict | None):
    """The run's stored assembly walk as links.build takes it
    (assembly_walk.for_links on the walk's map index, with the raise and
    hops in `walking`), or None: no walk stored, or none asked for."""
    if walking is None or not isinstance(run.get("assembly_walk"), dict) or not run["assembly_walk"]:
        return None
    return walking["module"].for_links(run["assembly_walk"], walking["index"], walking["raise"], walking["hops"])


def walk_metrics(items: list[dict], walk) -> dict:
    """What the walk did in one case-run: its panels and yes answers, the
    linked parts a yes sign lifted or opened (signs_yes), the second hops."""
    answers = walk.answers if walk is not None else {}
    return {
        "used": walk is not None,
        "panels": len(answers),
        "yes": sum(1 for rec in answers.values() for got in rec.get("signs", {}).values() if got.get("answer") == "yes"),
        "signs_yes_items": sum(1 for it in items if it.get("signs_yes")),
        "hop2": sum(1 for it in items if it.get("hop") == 2),
    }


def review_metrics(items: list[dict], listed, others, photos, intact, table, opts, review, walk=None) -> dict:
    """K4's numbers for one case-run: the strip-down block (folded) and the
    strip-down parts shown; the cap hits -- the parts intake_links_max (and
    the folded block's own cap) cut, beside a build without those caps, by
    seen + likely and strip-down -- and what the per-group cap cut, beside
    a build without that one too; the items per group. Pure: two more
    builds, on copies."""
    keys = {it["key"] for it in items}
    free = links.Options(**{**opts.__dict__, "max_items": UNCAPPED, "stripdown_max": UNCAPPED})
    loose = links.Options(**{**free.__dict__, "assembly_max": UNCAPPED})
    uncapped, _ = _shown(copy.deepcopy(listed), copy.deepcopy(others), photos, intact, table, free, review, walk)
    ungrouped, _ = _shown(copy.deepcopy(listed), copy.deepcopy(others), photos, intact, table, loose, review, walk)
    cut = [it for it in uncapped if it["key"] not in keys]
    free_keys = {it["key"] for it in uncapped}
    groups: dict[str, int] = {}
    for it in items:
        groups[links.group_of(it)] = groups.get(links.group_of(it), 0) + 1
    return {
        "folded": sum(1 for it in items if it.get("folded")),
        "stripdown_shown": sum(1 for it in items if it.get("bucket") == "strip_down"),
        "cap_cut_seen_likely": sum(1 for it in cut if it.get("bucket") in ("seen", "likely")),
        "cap_cut_stripdown": sum(1 for it in cut if it.get("bucket") == "strip_down"),
        "cap_hit": bool(cut),
        "group_cut": sum(1 for it in ungrouped if it["key"] not in free_keys),
        "groups": len(groups),
        "group_max": max(groups.values()) if groups else 0,
        "per_group": dict(sorted(groups.items())),
    }


def replay_run(run: dict, case: dict, table: dict, opts: links.Options, inside: set[str], review=None,
               walking=None) -> dict:
    """One stored case-run: the linked parts build gives, scored. The list,
    the suggestions, the photos and the intact lists go in as they are
    (never copied), so a change build made to any of them shows:
    damage_list_identical. review (any K4 flag): the car map's facts, and
    the review metrics and fields. walking (a stored walk in the files,
    not --no-walk): the run's walk, if it has one, and its fields."""
    listed = listed_of(run)
    others, others_from = suggestions_of(run)
    photos = run.get("sorted_photos") or []
    intact = zone_intact_of(run)
    walk = walk_of(run, walking)
    before = json.dumps([listed, others, photos, intact, run.get("assembly_walk")], sort_keys=True)
    stored = json.dumps(run.get("drafted_items"), sort_keys=True)
    # second_look._store ("on"): a link on the list, or dismissed, goes.
    items, joins = _shown(listed, others, photos, intact, table, opts, review, walk)
    metrics = (review_metrics(items, listed, others, photos, intact, table, opts, review, walk)
               if review is not None else None)
    identical = (json.dumps([listed, others, photos, intact, run.get("assembly_walk")], sort_keys=True) == before
                 and json.dumps(run.get("drafted_items"), sort_keys=True) == stored)
    drafted = [str(i.get("part")) for i in listed if i.get("part")]
    fields = ROW_FIELDS + (REVIEW_ROW_FIELDS if review is not None else ())
    if walking is not None:
        fields += tuple(f for f in WALK_ROW_FIELDS if f not in fields)
    rows = [{k: copy.deepcopy(it.get(k)) for k in fields} for it in items]
    block = bench.score_links(case["parts"], drafted, [o["part"] for o in others], rows, inside=inside)
    block.update(zone_joined=len(joins), suggestions_from=others_from, damage_list_identical=identical)
    out = {"case": run.get("case"), "parts": bench.score_parts(case["parts"], drafted), "drafted_parts": drafted,
           "suggested": [o["part"] for o in others], "links": block, "link_suggestions": rows,
           "joins": {k: v["linked_from"] for k, v in sorted(joins.items())}}
    if metrics is not None:
        out["review"] = metrics
    if walking is not None:
        out["walk"] = walk_metrics(items, walk)
    return out


def replay(sources: list[tuple[str, dict]], key: dict[str, dict], table: dict, opts: links.Options,
           review=None, walking=None) -> tuple[list, list]:
    """(replayed case-runs, sources' notes) over the files in order. Each
    file's repetitions are 4-case runs, numbered on across the files (rep),
    with the file and its own number kept (source, source_rep)."""
    inside = bench.hidden_children(table)
    runs, notes = [], []
    offset = 0
    for name, doc in sources:
        stored = doc.get("runs")
        stored = stored if isinstance(stored, list) else doc.get("results") or []
        reps = sorted({int(r.get("rep") or 1) for r in stored if isinstance(r, dict)})
        number = {rep: offset + i + 1 for i, rep in enumerate(reps)}
        skipped = []
        for r in stored:
            if not isinstance(r, dict):
                continue
            rep = int(r.get("rep") or 1)
            case = key.get(r.get("case"))
            if case is None:
                skipped.append({"case": r.get("case"), "rep": rep, "why": "not in the key"})
                continue
            if not bench.run_ok(r):
                skipped.append({"case": r.get("case"), "rep": rep, "why": (r.get("error") or "no list")[:200]})
                continue
            out = replay_run(r, case, table, opts, inside, review, walking_for(walking, doc))
            out.update(rep=number[rep], source=name, source_rep=rep)
            runs.append(out)
        notes.append({"file": name, "label": doc.get("label"), "runs": len(stored), "replayed": len(stored) - len(skipped),
                      "reps": [number[rep] for rep in reps], "skipped": skipped})
        offset += len(reps)
    return runs, notes


# ---------------------------------------------------------------- printing


def _cells(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows]


HEAD = (("links", "link_shown"), ("real", "link_real"), ("wrong side", "link_wrong_side"), ("extra", "link_extra"),
        ("wrong/real", "link_wrong_per_real"), ("strip-down wrong", "link_strip_wrong"),
        ("list+sugg", "lz_found"), ("+links", "lzl_found"), ("of", "link_expected"),
        ("inside l+s", "lz_inside_found"), ("inside +links", "lzl_inside_found"), ("inside of", "link_inside_expected"))


def summary_lines(summary: dict) -> list[str]:
    """Per case (mean [min-max] over its runs) and TOTAL (per 4-case run)."""
    rows = [["case", "runs"] + [h for h, _ in HEAD]]
    for name, c in summary["cases"].items():
        rows.append([str(name), str((c["metrics"].get("link_shown") or {}).get("n", 0))]
                    + [bench._fmt(c["metrics"].get(k)) for _, k in HEAD])
    total = summary["total"]
    rows.append([f"TOTAL ({len(summary['complete_reps'])} complete 4-case runs)", ""]
                + [bench._fmt(total.get(k)) for _, k in HEAD])
    return _cells(rows)


def group_lines(runs: list[dict]) -> list[str]:
    """By kind of link, bucket and hit level, summed over every case-run:
    shown, real, wrong side, extra."""
    out = []
    for field, _, names in bench.LINK_GROUPS:
        rows = [[field, "shown", "real", "wrong side", "extra"]]
        for name in names:
            got = [((r["links"].get(f"by_{field}") or {}).get(name) or {}) for r in runs]
            rows.append([name] + [str(sum(g.get(k, 0) for g in got)) for k in ("shown",) + bench.LINK_TRUTHS])
        out += _cells(rows) + [""]
    return out[:-1]


def review_lines(runs: list[dict]) -> list[str]:
    """The review metrics (K4) per case-run: strip-down block, cap hits,
    items per group."""
    rows = [["case", "rep", "shown", "folded", "strip-down shown", "cap cut s+l", "cap cut strip", "cap hit",
             "group cut", "groups", "most in a group"]]
    for r in runs:
        m = r.get("review") or {}
        rows.append([str(r.get("case")), str(r.get("rep")), str(r["links"]["shown"]), str(m.get("folded", 0)),
                     str(m.get("stripdown_shown", 0)), str(m.get("cap_cut_seen_likely", 0)),
                     str(m.get("cap_cut_stripdown", 0)), "yes" if m.get("cap_hit") else "no",
                     str(m.get("group_cut", 0)), str(m.get("groups", 0)), str(m.get("group_max", 0))])
    hits = sum(1 for r in runs if (r.get("review") or {}).get("cap_hit"))
    return _cells(rows) + [f"cap hit in {hits} of {len(runs)} case-run(s)"]


def review_summary(runs: list[dict]) -> dict:
    """The review metrics summed over the case-runs (and the most per group)."""
    ms = [r.get("review") or {} for r in runs]
    total = {k: sum(int(m.get(k) or 0) for m in ms) for k in ("folded", "stripdown_shown", "cap_cut_seen_likely",
                                                                 "cap_cut_stripdown", "group_cut", "groups")}
    total.update(case_runs=len(ms), cap_hits=sum(1 for m in ms if m.get("cap_hit")),
                 group_max=max((int(m.get("group_max") or 0) for m in ms), default=0),
                 folded_max=max((int(m.get("folded") or 0) for m in ms), default=0))
    return total


def variant_lines(name: str, entry: dict) -> list[str]:
    s = entry["summary"]
    total = s["total"]
    shown = [r["links"]["shown"] for r in entry["runs"]]
    head = ("frozen L1 knobs (the headline)" if "review" not in entry else
            "frozen knobs with the review flags above (K4: not the L1 headline)") if name == "default" else \
        f"pre-registered, sensitivity only: {', '.join(f'{k} {v}' for k, v in VARIANTS[name].items())}"
    out = [f"== variant {name}: {head}",
           f"   {len(entry['runs'])} case-run(s), {len(s['complete_reps'])} complete 4-case run(s); links shown per "
           f"case: median {statistics.median(shown) if shown else '-'}, max {max(shown) if shown else '-'}"]
    if total:
        out.append(f"   per 4-case run: real {bench._fmt(total.get('link_real'))}, wrong side "
                   f"{bench._fmt(total.get('link_wrong_side'))}, extra {bench._fmt(total.get('link_extra'))}, "
                   f"wrong/real {bench._fmt(total.get('link_wrong_per_real'))}; recall list+sugg "
                   f"{bench._fmt(total.get('lz_recall'))} -> +links {bench._fmt(total.get('lzl_recall'))}, inside "
                   f"{bench._fmt(total.get('lz_inside_recall'))} -> {bench._fmt(total.get('lzl_inside_recall'))}")
    out += ["", *summary_lines(s), "", *group_lines(entry["runs"])]
    if "review" in entry:
        out += ["", "   review screen (K4): " + ", ".join(f"{k} {v}" for k, v in entry["review"].items()), "",
                *review_lines(entry["runs"])]
    return out


# ---------------------------------------------------------------- main


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="replay_links.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("results", type=Path, nargs="+", help="stored benchmark result files (benchmark_intake.py --out)")
    ap.add_argument("--key", type=Path, default=bench._env_path("BENCH_KEY"), help="the answer key (key.json)")
    ap.add_argument("--out", type=Path, help="the replay's JSON (compare.py and gate.py --suggestions read it)")
    ap.add_argument("--variant", action="append", choices=sorted(VARIANTS) + ["all"],
                    help="also a pre-registered variant (repeatable; 'all': every one); the frozen defaults always run")
    ap.add_argument("--label", help="name of this replay (default: the --out file name)")
    ap.add_argument("--ref", help="git ref (and commit) the code came from")
    ap.add_argument("--freeze", type=Path, help="a freeze file (L1-freeze.md) to check the table against instead of "
                                                "the built-in L1 values")
    ap.add_argument("--not-frozen-ok", action="store_true",
                    help="replay a table or knobs that are not the freeze's (every number labelled so)")
    k4 = ap.add_argument_group("the car map's review screen (K4); any of these adds the review metrics")
    k4.add_argument("--car-map", metavar="PATH|from-l1|off",
                    help="build from this car map's table and facts ('from-l1': L1 converted; 'off': L1)")
    k4.add_argument("--links-order", choices=config.LINKS_ORDERS, help="intake_links_order (default table)")
    k4.add_argument("--assembly-max", type=int, metavar="N", help="intake_links_assembly_max (default 4)")
    k4.add_argument("--stripdown-cap", choices=config.STRIPDOWN_CAPS, help="intake_links_stripdown_cap (default in)")
    k4.add_argument("--stripdown-max", type=int, metavar="N", help="intake_links_stripdown_max (default 12)")
    wk = ap.add_argument_group("the assembly walk (K3b): a run stored with its walk is replayed with it")
    wk.add_argument("--no-walk", action="store_true", help="replay the same reads without their stored walks")
    wk.add_argument("--walk-raise", choices=config.WALK_RAISES,
                    help="intake_walk_raise for the stored walks (default: what the file recorded, else one)")
    wk.add_argument("--link-hops", type=int, choices=config.LINK_HOPS,
                    help="intake_link_hops for the stored walks (default: what the file recorded, else 1)")
    args = ap.parse_args(argv)
    for name in ("assembly_max", "stripdown_max"):
        value = getattr(args, name)
        if value is not None:
            try:
                setattr(args, name, config.normal_count(value, f"--{name.replace('_', '-')}"))
            except ValueError as exc:
                ap.error(str(exc))
    return args


def walking_for(walking: dict | None, doc: dict) -> dict | None:
    """The walk knobs for one stored file: --walk-raise / --link-hops, else
    what the file recorded (ai_settings), else one / 1."""
    if walking is None:
        return None
    recorded = doc.get("ai_settings") if isinstance(doc.get("ai_settings"), dict) else {}
    raise_mode = walking["raise_flag"] or recorded.get("intake_walk_raise") or "one"
    hops = walking["hops_flag"] or recorded.get("intake_link_hops") or 1
    return {**walking, "raise": raise_mode, "hops": int(hops)}


def walk_setup(args: argparse.Namespace, sources: list[tuple[str, dict]], review: dict | None) -> dict | None:
    """The stored walks' replay setup, or None (no run keeps a walk, or
    --no-walk): the walk's map index -- the --car-map map's when it has
    signs, else the app's car map's -- and the flags. Exits 2 on a walk
    flag with nothing to act on, or no map with signs to read the walks
    against."""
    flags = args.walk_raise is not None or args.link_hops is not None
    stored = sum(1 for _, doc in sources for r in (doc.get("runs") or doc.get("results") or [])
                 if isinstance(r, dict) and isinstance(r.get("assembly_walk"), dict) and r["assembly_walk"])
    if args.no_walk:
        if flags:
            print("replay_links: --no-walk with --walk-raise / --link-hops: one or the other", file=sys.stderr)
            sys.exit(2)
        return None
    if not stored:
        if flags:
            print("replay_links: --walk-raise / --link-hops: no stored run keeps an assembly walk", file=sys.stderr)
            sys.exit(2)
        return None
    from app.intake import assembly_walk, car_map

    cm = (review or {}).get("map")
    index = assembly_walk.index_for(cm) if isinstance(cm, dict) else None
    where = review.get("car_map") if index is not None else None
    if index is None:
        index, where = assembly_walk.index_for(car_map.MAP), "app/intake/car_map.json"
    if index is None:
        print("replay_links: the stored walks need a car map with signs (--car-map PATH, or "
              "app/intake/car_map.json); none loaded", file=sys.stderr)
        sys.exit(2)
    versions = sorted({str((doc.get("ai_settings") or {}).get("car_map_version")) for _, doc in sources
                       if isinstance(doc.get("ai_settings"), dict) and (doc.get("ai_settings") or {}).get("car_map_version")})
    return {"module": assembly_walk, "index": index, "map": where, "version": index.version,
            "raise_flag": args.walk_raise, "hops_flag": args.link_hops, "runs_with_walk": stored,
            "recorded_versions": versions}


def review_setup(args: argparse.Namespace) -> tuple[dict | None, dict | None, dict]:
    """(the review dict replay_run takes -- None when no K4 flag was given,
    the table to build from -- None: L1 --, the Options changes). Exits 2
    when --car-map names a map that doesn't load or compile."""
    change = {opt: getattr(args, flag) for flag, opt in REVIEW_FLAGS if getattr(args, flag) is not None}
    if args.car_map is None and not change:
        return None, None, {}
    review: dict = {"car_map": "off", "info": None, "map": None, "module": None}
    table = None
    if args.car_map not in (None, "off"):
        from app.intake import car_map

        if args.car_map == "from-l1":
            cm, where = car_map.from_l1(links.TABLE), "from-l1 (L1 converted)"
            sha = None
        else:
            path = Path(args.car_map)
            cm, where, sha = car_map.load(path), str(path), car_map.sha256_lf(path)
        if cm is None:
            print(f"replay_links: the car map {args.car_map} doesn't load (see the log)", file=sys.stderr)
            sys.exit(2)
        compiled = car_map.links_table(cm)
        problems = links.validate_table(compiled)
        if problems:
            print(f"replay_links: the car map's table fails the check: {problems[0]}", file=sys.stderr)
            sys.exit(2)
        table = compiled
        review.update(car_map=where, version=cm.get("version"), sha256_lf=sha, links=len(compiled["links"]),
                      info=car_map.review_lookup(cm), map=cm, module=car_map,
                      map_frozen=car_map.frozen_as(cm.get("version"), sha))
    return review, table, change


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.key is None:
        sys.exit("needs --key")
    if args.out is not None and any(args.out.resolve() == r.resolve() for r in args.results):
        sys.exit("--out must be another file: the stored results are kept as they are")
    version, shas = (parse_freeze(args.freeze.read_text(encoding="utf-8")) if args.freeze else (None, None))
    freeze = check_freeze(version, shas)
    if not freeze["ok"]:
        bad = [p for p, f in freeze["files"].items() if not f["ok"]] + [k for k, v in freeze["knobs"].items() if not v["ok"]]
        if freeze["version"] != freeze["frozen_version"]:
            bad.insert(0, f"version {freeze['version']} (frozen {freeze['frozen_version']})")
        if not args.not_frozen_ok:
            print(f"replay_links: not the frozen {freeze['frozen_version']} setup: {', '.join(bad)}", file=sys.stderr)
            return 2
        print(f"WARNING: NOT the frozen {freeze['frozen_version']} setup ({', '.join(bad)}): no headline number")
    key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    sources = [(path.name, json.loads(path.read_text(encoding="utf-8"))) for path in args.results]
    asked = set(args.variant or [])
    if "all" in asked:
        asked = set(VARIANTS)
    names = ["default"] + [name for name in VARIANTS if name in asked and name != "default"]
    review, review_table, change = review_setup(args)
    table = review_table if review_table is not None else links.TABLE
    walking = walk_setup(args, sources, review)
    entries = {}
    notes = []
    for name in names:
        opts = frozen_options(**VARIANTS[name], **change)
        runs, notes = replay(sources, key, table, opts, review, walking)
        # Today's options block holds the seven contract 10 knobs: the K4
        # four join it only when a K4 flag was given.
        shown_opts = {k: list(v) if isinstance(v, tuple) else v for k, v in opts.__dict__.items()
                      if review is not None or k not in K4_OPTIONS}
        entries[name] = {"options": shown_opts, "runs": runs, "summary": bench.summarize(runs)}
        if review is not None:
            entries[name]["review"] = review_summary(runs)
    identical = all(r["links"]["damage_list_identical"] for e in entries.values() for r in e["runs"])
    loaded = sorted(m for m in sys.modules if blocked(m))
    doc = {
        "kind": "replay_links", "label": args.label or (args.out.stem if args.out else "replay_links"),
        "git_ref": args.ref, "key": args.key.name, "sources": notes,
        "table": freeze, "frozen": freeze["ok"] and review_table is None, "knobs": FROZEN_KNOBS,
        "guard": dict(GUARD, loaded_after=loaded), "gemini_calls": 0,
        "variants_preregistered": list(VARIANTS), "damage_list_identical": identical,
        "runs": entries["default"]["runs"], "summary": entries["default"]["summary"],
        "options": entries["default"]["options"],
        "variants": {n: e for n, e in entries.items() if n != "default"},
    }
    if walking is not None:
        ws = [r.get("walk") or {} for r in entries["default"]["runs"]]
        doc["walk"] = {"map": walking["map"], "version": walking["version"],
                       "recorded_versions": walking["recorded_versions"],
                       "flags": {"walk_raise": args.walk_raise, "link_hops": args.link_hops},
                       "runs_with_walk": walking["runs_with_walk"],
                       "summary": {"case_runs_walked": sum(1 for w in ws if w.get("used")),
                                   "yes": sum(int(w.get("yes") or 0) for w in ws),
                                   "signs_yes_items": sum(int(w.get("signs_yes_items") or 0) for w in ws),
                                   "hop2": sum(int(w.get("hop2") or 0) for w in ws)}}
    if review is not None:
        doc["review_screen"] = {
            "car_map": review["car_map"], "version": review.get("version"), "sha256_lf": review.get("sha256_lf"),
            "map_frozen": review.get("map_frozen"),
            "links": review.get("links"), "flags": {flag: getattr(args, flag) for flag, _ in REVIEW_FLAGS},
            "summary": entries["default"]["review"]}
    print(f"REPLAY linked parts: table {freeze['version']} "
          f"({'frozen' if freeze['ok'] else 'NOT FROZEN'}"
          f"{'; the car map built instead, see below' if review_table is not None else ''}), 0 Gemini calls (guard on), "
          f"{len(sources)} file(s), damage list {'identical' if identical else 'CHANGED'}")
    if review is not None:
        flags = ", ".join(f"{flag.replace('_', '-')} {getattr(args, flag)}" for flag, _ in REVIEW_FLAGS
                          if getattr(args, flag) is not None)
        frozen = {True: ", the frozen file (K1-freeze.md)", False: ", NOT the frozen file of that version",
                  None: ""}[review.get("map_frozen")]
        print(f"   review screen (K4): car map {review['car_map']}"
              + (f" ({review.get('version')}, {review.get('links')} links, sha256 LF {review.get('sha256_lf')}{frozen})"
                 if review["car_map"] != "off" else "") + (f"; {flags}" if flags else ""))
    if walking is not None:
        w = doc["walk"]
        stale = [v for v in w["recorded_versions"] if v != w["version"]]
        print(f"   assembly walk: {w['runs_with_walk']} stored run(s) with a walk, read against {w['map']} "
              f"({w['version']}); raise {args.walk_raise or 'as recorded'}, hops {args.link_hops or 'as recorded'}; "
              f"{w['summary']['case_runs_walked']} case-run(s) walked, {w['summary']['signs_yes_items']} part(s) "
              f"with a yes sign, {w['summary']['hop2']} second hop(s)"
              + (f"; WARNING: the files' walks were asked from map {', '.join(stale)}" if stale else ""))
    for note in notes:
        print(f"   {note['file']} ({note['label']}): {note['replayed']}/{note['runs']} case-runs replayed, "
              f"4-case runs {note['reps']}" + (f"; skipped {len(note['skipped'])}: "
                                               + "; ".join(f"run {s['rep']} {s['why']}" for s in note["skipped"])
                                               if note["skipped"] else ""))
    for name, entry in entries.items():
        print()
        for line in variant_lines(name, entry):
            print(line)
    if args.out is not None:
        bench._write_json(args.out, doc)
        print("\nwritten", args.out)
    if loaded:
        print(f"replay_links: GUARD FAILED, loaded {loaded}", file=sys.stderr)
        return 4
    if not identical:
        print("replay_links: the damage list CHANGED in a replay -- links must never touch it", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
