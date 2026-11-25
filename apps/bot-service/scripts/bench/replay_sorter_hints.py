"""OFFLINE, 0 Gemini calls: Detection Round 1's A1 and A2 (app/intake/
sorter_hints.py) replayed on stored cases ("trails"), and what A2 strict
does to the linked parts.

    python scripts/bench/replay_sorter_hints.py TRAIL.json [MORE.json ...] [--out OUT.json] [--cap N]
        [--links off|shadow|on] [--links-max N] [--links-intact demote|drop|ignore]
        [--stripdown-cap in|out] [--stripdown-max N] [--car-map off|shadow|on]
        [--links-order table|prior] [--assembly-max N]

Each trail is one case, as its draft was stored (made by the lead from the
database; see the fixture scripts/fixtures/sorter_hints_sample.json):

    {
      "case": "name",                       (optional: the file's name)
      "photos": [{"label": "P01", "kind": "damage", "area": "rear",
                  "part": "Rear Bumper", "damage": true, "note": "...",
                  "read": true, "box": [y0, x0, y1, x1] | null,
                  "quality": "good", "excluded": false}, ...],
                                            (camera order; quality and
                                            excluded optional)
      "damage_summary": [{"part": ...}, ...],   (the AI draft's items)
      "second_look": {"items": [...], "dismissed": [...]},
      "runs": [{"kind": "read_zones", "photos_sent": {"zones": [
                  {"zone": "rear", "side_seen": "rear", "found": 2,
                   "intact": ["Rear Windscreen", ...], "photos": [...]}]}}],
      "zone_intact": {"zone:rear": [...]},  (optional: else from "runs")
      "vehicle_info": {"make", "model", "body_type"},   (optional: only the
                                            car map's body gate reads it)
      "assembly_walk": {...},               (optional: only with the walk on)
      "settings": {"intake_links_max": 12, ...}   (optional: the linked
                                            parts' settings that run used;
                                            LINK_SETTINGS names only)
    }

A1 and A2's suggestions. What _store hands sorter_hints.find, rebuilt from
the trail: the photos (IntakePhoto's fields; "damage" is shows_damage,
"box" damage_box), the list's parts (second_look.listed_parts on
damage_summary), the suggestions shown or decided (second_look.items, but
a "sorter" one -- this replay's own), the dismissed, and ai_draft[
"zone_intact"] (the last read_zones run's zones that answered:
{"zone:<zone>": intact}). Then sorter_hints.find -- the pipeline's own
code, nothing reimplemented -- three ways: A1 alone ("a1":
intake_sorter_hints on, intake_intact_rule old), A2 alone ("a2": strict,
A1 off) and both ("both"), and A2's overrides (sorter_hints.overridden:
each zone read's intact verdict a sorter photo contradicts). With strict
the linked parts change too (below): "a2" and "both" are found against
the strict linked parts when the rebuild of the old ones matches the
trail's stored linked parts exactly ("a2_links": "rebuilt"), else against
the stored ones ("stored").

The linked parts, old and strict ("links_diff"). second_look.build_links
-- what _store builds them with, without the database -- twice, then
second_look.open_links (the listed and dismissed left out, as _store
shows them): once with the zone reads' intact lists as stored ("old"),
once with sorter_hints.strict_intact applied ("strict"). It reads, from
the trail:
  - damage_summary: the parents -- part, condition_tokens, condition,
    damage_type, severity, remarks (how hard each was hit), side_inferred,
    ai_confidence, seen_in, found_by (whether its side is firm),
    photo_reference and evidence (its photo), other_part_name (its key);
  - second_look.items: the open suggestions that are not linked parts (a
    linked part that is one joins it) and, for the check, the open linked
    parts stored (source "link"); second_look.dismissed;
  - photos: kind, area, part, damage, quality, excluded (the "seen"
    bucket, the crush rule's underbody photo);
  - zone_intact (from runs, as above);
  - vehicle_info, only with the car map on; assembly_walk, only with the
    walk on.
The settings: production's by default (intake_links on, intake_links_max
12, intake_links_stripdown_cap out; every other one config.py's default,
intake_links_intact "demote" and intake_car_map "off" among them), then
the trail's "settings", then the flags given here. The parts catalogue's
diagram neighbours (intake_links_catalogue) are in the database only:
never rebuilt.

Output (--out, and a short table on stdout):

    {"tool": "replay_sorter_hints", "version": 2, "cap": 6,
     "cases": [{"case", "photos",
                "zone_intact": {...},
                "a1": [ROW, ...], "a2": [ROW, ...], "both": [ROW, ...],
                "a2_links": "rebuilt" | "stored",
                "a2_overrides": [{"read", "part", "photo"}],
                "skipped": {"a1": {...}, "a2": {...}, "both": {...}},
                "links_diff": {"old": [part, ...], "strict": [part, ...],
                               "gained": [...], "lost": [...], "reordered": bool,
                               "check": {"stored", "matched", "unmatched_rebuilt",
                                         "unmatched_stored", "missing_inputs": [...]},
                               "settings": {...}}}],
     "totals": {"a1", "a2", "both", "a2_overrides", "links_gained", "links_lost"}}
    ROW = {"part", "source": "sorter", "photo": "P07", "reason",
           "via": "part" | "note", "intact_in": [zone reads],
           "read": did the main read get that photo (the trail's "read"),
           "seen_in": ["P07", ...], "box": the sorter's box on that photo
           when it is round this part (its tag's, or the only one), else null}
"old" and "strict" are the linked parts in the order shown (a folded
strip-down block's after the rest); "reordered": the parts both have are
in another order. "check": how the old rebuild compares with the open
linked parts stored in the trail (counts by part), and the inputs the
rebuild needed that the trail hasn't got.

The guard: run as a script, no network connection can be made (a socket
that tries raises NoNetwork). Deterministic: the same files give the same
output, byte for byte. Made-up cases only in git; the real trails stay on
the lead's machine.
"""
import argparse
import contextlib
import copy
import json
import socket
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = SCRIPTS_DIR.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

VERSION = 2


class NoNetwork(RuntimeError):
    """The replay tried to open a connection."""


def _no_network(*_args, **_kwargs):
    raise NoNetwork("replay_sorter_hints makes no network connection")


def install_guard() -> None:
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    socket.socket.connect_ex = _no_network  # type: ignore[method-assign]
    socket.create_connection = _no_network  # type: ignore[assignment]


from app import config  # noqa: E402
from app.ai import second_look  # noqa: E402
from app.ai.second_look import listed_parts  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import links, sorter_hints  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402

# The settings the linked parts are built with (links.options, the car map,
# the assembly walk), and production's where it isn't config.py's default.
LINK_SETTINGS = ("intake_links", "intake_links_max", "intake_links_intact", "intake_links_stripdown_cap",
                 "intake_links_stripdown_max", "intake_car_map", "intake_links_order", "intake_links_assembly_max",
                 "intake_links_types", "intake_links_side", "intake_links_unknown_hit", "intake_links_catalogue",
                 "intake_assembly_walk", "intake_walk_raise", "intake_link_hops")
PRODUCTION = {"intake_links": "on", "intake_links_max": 12, "intake_links_stripdown_cap": "out"}
# The flags, by setting.
FLAGS = {"links": "intake_links", "links_max": "intake_links_max", "links_intact": "intake_links_intact",
         "stripdown_cap": "intake_links_stripdown_cap", "stripdown_max": "intake_links_stripdown_max",
         "car_map": "intake_car_map", "links_order": "intake_links_order", "assembly_max": "intake_links_assembly_max"}
# Fields that say how hard a listed part was hit (links.hit_of).
HIT_FIELDS = ("condition_tokens", "condition", "damage_type", "severity", "remarks")


class SettingsError(ValueError):
    """A linked-parts setting (a flag or a trail's) that config refuses."""


def default_settings() -> dict:
    """Production's linked-parts settings: PRODUCTION, else config.py's
    defaults (never this shell's environment)."""
    return {name: PRODUCTION.get(name, config.Settings.model_fields[name].default) for name in LINK_SETTINGS}


def checked(name: str, value):
    """A setting's value through config's own field and validators."""
    from pydantic import ValidationError

    try:
        return getattr(config.Settings(**{name: value}), name)
    except ValidationError as exc:
        why = "; ".join(str(e.get("msg")) for e in exc.errors()) or str(exc)
        raise SettingsError(f"{name}={value!r}: {why[:200]}") from None


def settings_for(trail: dict, flags: dict) -> dict:
    """production's < the trail's "settings" (LINK_SETTINGS names only) <
    the flags given."""
    values = default_settings()
    own = trail.get("settings") if isinstance(trail.get("settings"), dict) else {}
    for name in LINK_SETTINGS:
        if name in own:
            values[name] = checked(name, own[name])
    for name, value in flags.items():
        values[name] = checked(name, value)
    return values


@contextlib.contextmanager
def with_settings(values: dict):
    old = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        setattr(settings, name, value)
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(settings, name, value)


# ------------------------------------------------------------ the trail


def photos_of(trail: dict) -> list[dict]:
    """The trail's photos as sorter_hints reads IntakePhoto rows: id = label."""
    out = []
    for n, p in enumerate(trail.get("photos") or []):
        if not isinstance(p, dict) or not p.get("label"):
            continue
        out.append({
            "id": str(p["label"]), "position": n, "kind": p.get("kind"), "area": p.get("area"),
            "part": p.get("part"), "note": p.get("note"), "shows_damage": p.get("damage") is True,
            "quality": p.get("quality") or "good", "excluded": bool(p.get("excluded")),
            "damage_box": p.get("box"), "read": bool(p.get("read")),
        })
    return out


def zone_runs(trail: dict) -> list | None:
    """The last run's zone entries, None when no run has any."""
    zones = None
    for run in trail.get("runs") or []:
        sent = run.get("photos_sent") if isinstance(run, dict) else None
        if isinstance(sent, dict) and isinstance(sent.get("zones"), list):
            zones = sent["zones"]
    return zones


def zone_intact_of(trail: dict) -> dict[str, list[str]]:
    """ai_draft["zone_intact"]: the trail's own, else the last run with zone
    entries -- each zone that answered (an "intact" list) by "zone:<zone>"."""
    if isinstance(trail.get("zone_intact"), dict):
        return {str(k): [p for p in v if isinstance(p, str)] for k, v in trail["zone_intact"].items()
                if isinstance(v, list)}
    out: dict[str, list[str]] = {}
    for entry in zone_runs(trail) or []:
        if isinstance(entry, dict) and entry.get("zone") and isinstance(entry.get("intact"), list):
            out[f"zone:{entry['zone']}"] = [p for p in entry["intact"] if isinstance(p, str)]
    return out


def look_of(trail: dict) -> tuple[list[dict], list[str]]:
    """(the suggestions shown or decided, the dismissed) -- a "sorter" one
    left out: this replay makes those."""
    look = trail.get("second_look") if isinstance(trail.get("second_look"), dict) else {}
    items = [it for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")
             and it.get("source") != sorter_hints.SOURCE]
    return items, [str(k) for k in look.get("dismissed") or [] if isinstance(k, str) and k]


def listed_of(trail: dict) -> list[dict]:
    return [copy.deepcopy(it) for it in trail.get("damage_summary") or [] if isinstance(it, dict) and it.get("part")]


def _label(ref) -> str | None:
    ref = str(ref or "")
    return ref[1:] if ref.startswith("@") else (ref or None)


def rows(items: list[dict], found: "sorter_hints.Found", photos: dict[str, dict]) -> list[dict]:
    out = []
    for it in items:
        label = _label(it.get("photo_reference"))
        origin = found.origin.get(it["key"]) or {}
        photo = photos.get(label or "") or {}
        own = next((e for e in it.get("evidence") or [] if e.get("photo") == it.get("photo_reference")), {})
        out.append({
            "part": it["part"], "source": it.get("source"), "photo": label, "reason": it.get("reason"),
            "via": origin.get("via"), "intact_in": list(origin.get("reads") or []), "read": bool(photo.get("read")),
            "seen_in": [_label(r) for r in it.get("seen_in") or []], "box": own.get("box"),
        })
    return out


# ------------------------------------------------------------ the linked parts


def missing_inputs(trail: dict, listed: list[dict], photos: list[dict], stored: list[str], values: dict) -> list[str]:
    """What the linked parts' rebuild needs that the trail hasn't got."""
    out = []
    if listed and not any(name in it for it in listed for name in HIT_FIELDS):
        out.append("damage_summary's damage words (" + ", ".join(HIT_FIELDS) + "): every hit taken as "
                   f"intake_links_unknown_hit ({values['intake_links_unknown_hit']})")
    raw = [p for p in trail.get("photos") or [] if isinstance(p, dict)]
    if raw and not all(all(k in p for k in ("kind", "area", "part", "damage")) for p in raw):
        out.append("the photos' sort (kind, area, part, damage)")
    if zone_runs(trail) is None and not isinstance(trail.get("zone_intact"), dict):
        out.append("zone_intact (no read_zones run in the trail)")
    if values["intake_car_map"] != "off" and not isinstance(trail.get("vehicle_info"), dict):
        out.append("vehicle_info (the car map's body gate)")
    if values["intake_links_catalogue"]:
        out.append("the parts catalogue's diagram neighbours (intake_links_catalogue: in the database only)")
    if values["intake_assembly_walk"] == "on" and not isinstance(trail.get("assembly_walk"), dict):
        out.append("assembly_walk (intake_assembly_walk on)")
    if not stored:
        out.append("no open linked parts stored (links off on that run, or none)")
    return out


def links_diff(trail: dict, photos: list[dict], intact: dict, values: dict) -> tuple[dict, list[dict], bool]:
    """(links_diff, the strict linked parts as _store shows them, whether
    the old rebuild matches the stored ones exactly)."""
    look = trail.get("second_look") if isinstance(trail.get("second_look"), dict) else {}
    dismissed = [str(k) for k in look.get("dismissed") or [] if isinstance(k, str) and k]
    listed_items = listed_of(trail)
    vehicle = trail.get("vehicle_info") if isinstance(trail.get("vehicle_info"), dict) else {}
    data = {"damage_summary": listed_items, "vehicle_info": vehicle, "second_look": {"dismissed": dismissed}}
    listed = listed_parts(data)
    listed_keys = {item_key(it) for it in listed_items}
    items = []
    for it in look.get("items") or []:
        if isinstance(it, dict) and it.get("part") and it.get("source") != sorter_hints.SOURCE:
            it = copy.deepcopy(it)
            it["key"] = it.get("key") or item_key(it) or it["part"]
            items.append(it)
    open_items, _ = second_look.split_settled(items, data)
    kept = [it for it in open_items if it.get("source") != "link"]
    stored = [str(it["part"]) for it in open_items if it.get("source") == "link"]
    ai_draft = {"zone_intact": intact}
    if isinstance(trail.get("assembly_walk"), dict):
        ai_draft["assembly_walk"] = trail["assembly_walk"]
    built: dict[str, list[dict]] = {"old": [], "strict": []}
    if values["intake_links"] != "off":
        with with_settings(values):
            opts = links.options()
            tables = None
            if second_look._car_map_mode() != "off":
                from app.intake import car_map

                tables = car_map.link_tables(links.TABLE, vehicle)
            for name, strict in (("old", False), ("strict", True)):
                linked, _ = second_look.build_links(copy.deepcopy(listed_items), copy.deepcopy(kept), photos,
                                                    ai_draft, dismissed, tables, opts=opts, strict=strict)
                built[name] = second_look.open_links(linked, listed, listed_keys, dismissed)
    old = [str(it["part"]) for it in built["old"]]
    new = [str(it["part"]) for it in built["strict"]]
    both = set(old) & set(new)
    matched = len(set(old) & set(stored))
    check = {"stored": len(set(stored)), "matched": matched, "unmatched_rebuilt": len(set(old) - set(stored)),
             "unmatched_stored": len(set(stored) - set(old)),
             "missing_inputs": missing_inputs(trail, listed_items, photos, stored, values)}
    diff = {
        "old": old, "strict": new,
        "gained": [p for p in new if p not in set(old)], "lost": [p for p in old if p not in set(new)],
        "reordered": [p for p in old if p in both] != [p for p in new if p in both],
        "check": check, "settings": dict(sorted(values.items())),
    }
    exact = check["unmatched_rebuilt"] == 0 and check["unmatched_stored"] == 0
    return diff, built["strict"], exact


# ------------------------------------------------------------ one case


def replay_case(trail: dict, name: str, cap: int = sorter_hints.MAX_HINTS, flags: dict | None = None) -> dict:
    photos = photos_of(trail)
    labels = {p["id"]: p["id"] for p in photos}
    listed = listed_parts({"damage_summary": listed_of(trail)})
    shown, dismissed = look_of(trail)
    intact = zone_intact_of(trail)
    by_label = {p["id"]: p for p in photos}
    diff, strict_links, exact = links_diff(trail, photos, intact, settings_for(trail, flags or {}))
    # Under strict the linked parts are strict's: the stored open ones swapped
    # for the rebuild's, when the rebuild of the old ones matches them.
    if exact:
        stored_open = set(diff["old"])
        shown_strict = [it for it in shown if not (it.get("source") == "link" and it.get("part") in stored_open)]
        shown_strict += strict_links
    else:
        shown_strict = shown

    def run(hints: bool, strict: bool):
        return sorter_hints.find(photos, labels, listed=listed, shown=shown_strict if strict else shown,
                                 dismissed=dismissed, zone_intact=intact, hints=hints, strict_rule=strict, cap=cap)

    a1, a2, both = run(True, False), run(False, True), run(True, True)
    return {
        "case": name, "photos": len(photos), "zone_intact": intact,
        "a1": rows(a1.hints, a1, by_label),
        "a2": rows(a2.intact, a2, by_label),
        "both": rows(both.intact + both.hints, both, by_label),
        "a2_links": "rebuilt" if exact else "stored",
        "a2_overrides": [{**o, "photo": _label(o["photo"])} for o in sorter_hints.overridden(intact, photos)],
        "skipped": {"a1": dict(sorted(a1.skipped.items())), "a2": dict(sorted(a2.skipped.items())),
                    "both": dict(sorted(both.skipped.items()))},
        "links_diff": diff,
    }


def replay(paths: list[Path], cap: int = sorter_hints.MAX_HINTS, flags: dict | None = None) -> dict:
    cases = []
    for path in paths:
        trail = json.loads(Path(path).read_text(encoding="utf-8"))
        trails = trail if isinstance(trail, list) else [trail]
        for n, one in enumerate(trails):
            name = str(one.get("case") or (Path(path).stem if len(trails) == 1 else f"{Path(path).stem}#{n + 1}"))
            cases.append(replay_case(one, name, cap, flags))
    totals = {key: sum(len(c[key]) for c in cases) for key in ("a1", "a2", "both", "a2_overrides")}
    totals["links_gained"] = sum(len(c["links_diff"]["gained"]) for c in cases)
    totals["links_lost"] = sum(len(c["links_diff"]["lost"]) for c in cases)
    return {"tool": "replay_sorter_hints", "version": VERSION, "cap": cap, "cases": cases, "totals": totals}


def table(result: dict) -> list[str]:
    lines = [f"{'case':<28} {'photos':>6} {'A1':>4} {'A2':>4} {'both':>5} {'overrides':>9}"]
    for c in result["cases"]:
        lines.append(f"{c['case'][:28]:<28} {c['photos']:>6} {len(c['a1']):>4} {len(c['a2']):>4} {len(c['both']):>5} "
                     f"{len(c['a2_overrides']):>9}")
        for row in c["both"]:
            lines.append(f"    + {row['part']} ({row['photo']}, {row['via']}{', intact' if row['intact_in'] else ''}"
                         f"{', read' if row['read'] else ''})")
        d = c["links_diff"]
        gained = "; ".join(f"+{p}" for p in d["gained"])
        lost = "; ".join(f"-{p}" for p in d["lost"])
        changes = ", ".join(x for x in (gained, lost, "reordered" if d["reordered"] else "") if x) or "no change"
        ck = d["check"]
        lines.append(f"    links: old {len(d['old'])}, strict {len(d['strict'])} ({changes}); stored {ck['matched']}/"
                     f"{ck['stored']} matched, {ck['unmatched_rebuilt']} rebuilt only"
                     + (f"; missing: {len(ck['missing_inputs'])}" if ck["missing_inputs"] else ""))
    t = result["totals"]
    lines.append(f"{'total':<28} {'':>6} {t['a1']:>4} {t['a2']:>4} {t['both']:>5} {t['a2_overrides']:>9}"
                 f"   links +{t['links_gained']} -{t['links_lost']}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="replay_sorter_hints.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("trails", type=Path, nargs="+", help="trail JSON files, one case each (or a list of cases)")
    ap.add_argument("--out", type=Path, help="write the replay's JSON here")
    ap.add_argument("--cap", type=int, default=sorter_hints.MAX_HINTS, help="A1's cap a case (default 6)")
    lk = ap.add_argument_group("the linked parts' settings (default: production's, then the trail's \"settings\")")
    lk.add_argument("--links", choices=config.LINKS_MODES, help="intake_links (default on)")
    lk.add_argument("--links-max", type=int, metavar="N", help="intake_links_max (default 12)")
    lk.add_argument("--links-intact", choices=config.LINKS_INTACT, help="intake_links_intact (default config's)")
    lk.add_argument("--stripdown-cap", choices=config.STRIPDOWN_CAPS, help="intake_links_stripdown_cap (default out)")
    lk.add_argument("--stripdown-max", type=int, metavar="N", help="intake_links_stripdown_max (default config's)")
    lk.add_argument("--car-map", choices=config.CAR_MAP_MODES, help="intake_car_map (default config's)")
    lk.add_argument("--links-order", choices=config.LINKS_ORDERS, help="intake_links_order (default config's)")
    lk.add_argument("--assembly-max", type=int, metavar="N", help="intake_links_assembly_max (default config's)")
    args = ap.parse_args(argv)
    install_guard()
    if args.cap < 0:
        ap.error("--cap: 0 or more")
    flags = {setting: getattr(args, dest) for dest, setting in FLAGS.items() if getattr(args, dest) is not None}
    try:
        result = replay(args.trails, args.cap, flags)
    except SettingsError as exc:
        print(f"replay_sorter_hints: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print("\n".join(table(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
