"""OFFLINE, 0 Gemini calls: the linked parts (contract 10, app/intake/
links.py) replayed on stored benchmark runs, and scored against the key.

    python scripts/bench/replay_links.py RESULT.json [MORE.json ...] --key KEY.json [--out OUT.json]
        [--variant NAME ...] [--label L] [--ref R] [--freeze L1-freeze.md] [--not-frozen-ok]

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


def replay_run(run: dict, case: dict, table: dict, opts: links.Options, inside: set[str]) -> dict:
    """One stored case-run: the linked parts build gives, scored. The list,
    the suggestions, the photos and the intact lists go in as they are
    (never copied), so a change build made to any of them shows:
    damage_list_identical."""
    listed = listed_of(run)
    others, others_from = suggestions_of(run)
    photos = run.get("sorted_photos") or []
    intact = zone_intact_of(run)
    before = json.dumps([listed, others, photos, intact], sort_keys=True)
    stored = json.dumps(run.get("drafted_items"), sort_keys=True)
    items, joins = links.build_all(listed, others, photos, intact, table, opts, dismissed=())
    # second_look._store ("on"): a link on the list, or dismissed, goes.
    names = {str(i.get("part")) for i in listed}
    items = [it for it in items if not set(links.covers(it["part"])) & names]
    identical = (json.dumps([listed, others, photos, intact], sort_keys=True) == before
                 and json.dumps(run.get("drafted_items"), sort_keys=True) == stored)
    drafted = [str(i.get("part")) for i in listed if i.get("part")]
    rows = [{k: copy.deepcopy(it.get(k)) for k in ROW_FIELDS} for it in items]
    block = bench.score_links(case["parts"], drafted, [o["part"] for o in others], rows, inside=inside)
    block.update(zone_joined=len(joins), suggestions_from=others_from, damage_list_identical=identical)
    return {"case": run.get("case"), "parts": bench.score_parts(case["parts"], drafted), "drafted_parts": drafted,
            "suggested": [o["part"] for o in others], "links": block, "link_suggestions": rows,
            "joins": {k: v["linked_from"] for k, v in sorted(joins.items())}}


def replay(sources: list[tuple[str, dict]], key: dict[str, dict], table: dict, opts: links.Options) -> tuple[list, list]:
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
            out = replay_run(r, case, table, opts, inside)
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


def variant_lines(name: str, entry: dict) -> list[str]:
    s = entry["summary"]
    total = s["total"]
    shown = [r["links"]["shown"] for r in entry["runs"]]
    head = "frozen L1 knobs (the headline)" if name == "default" else \
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
    return ap.parse_args(argv)


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
    entries = {}
    notes = []
    for name in names:
        opts = frozen_options(**VARIANTS[name])
        runs, notes = replay(sources, key, links.TABLE, opts)
        entries[name] = {"options": {k: list(v) if isinstance(v, tuple) else v for k, v in opts.__dict__.items()},
                         "runs": runs, "summary": bench.summarize(runs)}
    identical = all(r["links"]["damage_list_identical"] for e in entries.values() for r in e["runs"])
    loaded = sorted(m for m in sys.modules if blocked(m))
    doc = {
        "kind": "replay_links", "label": args.label or (args.out.stem if args.out else "replay_links"),
        "git_ref": args.ref, "key": args.key.name, "sources": notes,
        "table": freeze, "frozen": freeze["ok"], "knobs": FROZEN_KNOBS,
        "guard": dict(GUARD, loaded_after=loaded), "gemini_calls": 0,
        "variants_preregistered": list(VARIANTS), "damage_list_identical": identical,
        "runs": entries["default"]["runs"], "summary": entries["default"]["summary"],
        "options": entries["default"]["options"],
        "variants": {n: e for n, e in entries.items() if n != "default"},
    }
    print(f"REPLAY linked parts: table {freeze['version']} "
          f"({'frozen' if freeze['ok'] else 'NOT FROZEN'}), 0 Gemini calls (guard on), "
          f"{len(sources)} file(s), damage list {'identical' if identical else 'CHANGED'}")
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
