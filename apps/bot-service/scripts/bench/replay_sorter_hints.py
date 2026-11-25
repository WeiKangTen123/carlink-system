"""OFFLINE, 0 Gemini calls: Detection Round 1's A1 and A2 (app/intake/
sorter_hints.py) replayed on stored cases ("trails").

    python scripts/bench/replay_sorter_hints.py TRAIL.json [MORE.json ...] [--out OUT.json] [--cap N]

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
      "zone_intact": {"zone:rear": [...]}   (optional: else from "runs")
    }

What _store hands sorter_hints.find, rebuilt from the trail: the photos
(IntakePhoto's fields; "damage" is shows_damage, "box" damage_box), the
list's parts (second_look.listed_parts on damage_summary), the
suggestions shown or decided (second_look.items, but a "sorter" one --
this replay's own), the dismissed, and ai_draft["zone_intact"] (the last
read_zones run's zones that answered: {"zone:<zone>": intact}). Then
sorter_hints.find -- the pipeline's own code, nothing reimplemented -- three
ways: A1 alone ("a1": intake_sorter_hints on, intake_intact_rule old), A2
alone ("a2": strict, A1 off) and both ("both"), and A2's overrides
(sorter_hints.overridden: each zone read's intact verdict a sorter photo
contradicts -- the linked parts strict no longer demotes or drops).

Output (--out, and a short table on stdout):

    {"tool": "replay_sorter_hints", "version": 1, "cap": 6,
     "cases": [{"case", "photos",
                "zone_intact": {...},
                "a1": [ROW, ...], "a2": [ROW, ...], "both": [ROW, ...],
                "a2_overrides": [{"read", "part", "photo"}],
                "skipped": {"a1": {...}, "a2": {...}, "both": {...}}}],
     "totals": {"a1": n, "a2": n, "both": n, "a2_overrides": n}}
    ROW = {"part", "source": "sorter", "photo": "P07", "reason",
           "via": "part" | "note", "intact_in": [zone reads],
           "read": did the main read get that photo (the trail's "read"),
           "seen_in": ["P07", ...], "box": the sorter's box on that photo
           when it is round this part (its tag's, or the only one), else null}

The guard: run as a script, no network connection can be made (a socket
that tries raises NoNetwork). Deterministic: the same files give the same output, byte for
byte. Made-up cases only in git; the real trails stay on the lead's machine.
"""
import argparse
import json
import socket
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
BOT_DIR = SCRIPTS_DIR.parent
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

VERSION = 1


class NoNetwork(RuntimeError):
    """The replay tried to open a connection."""


def _no_network(*_args, **_kwargs):
    raise NoNetwork("replay_sorter_hints makes no network connection")


def install_guard() -> None:
    socket.socket.connect = _no_network  # type: ignore[method-assign]
    socket.socket.connect_ex = _no_network  # type: ignore[method-assign]
    socket.create_connection = _no_network  # type: ignore[assignment]


from app.ai.second_look import listed_parts  # noqa: E402
from app.intake import sorter_hints  # noqa: E402


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


def zone_intact_of(trail: dict) -> dict[str, list[str]]:
    """ai_draft["zone_intact"]: the trail's own, else the last run with zone
    entries -- each zone that answered (an "intact" list) by "zone:<zone>"."""
    if isinstance(trail.get("zone_intact"), dict):
        return {str(k): [p for p in v if isinstance(p, str)] for k, v in trail["zone_intact"].items()
                if isinstance(v, list)}
    zones = None
    for run in trail.get("runs") or []:
        sent = run.get("photos_sent") if isinstance(run, dict) else None
        if isinstance(sent, dict) and isinstance(sent.get("zones"), list):
            zones = sent["zones"]
    out: dict[str, list[str]] = {}
    for entry in zones or []:
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


def replay_case(trail: dict, name: str, cap: int = sorter_hints.MAX_HINTS) -> dict:
    photos = photos_of(trail)
    labels = {p["id"]: p["id"] for p in photos}
    listed = listed_parts({"damage_summary": [it for it in trail.get("damage_summary") or [] if isinstance(it, dict)]})
    shown, dismissed = look_of(trail)
    intact = zone_intact_of(trail)
    by_label = {p["id"]: p for p in photos}

    def run(hints: bool, strict: bool):
        return sorter_hints.find(photos, labels, listed=listed, shown=shown, dismissed=dismissed, zone_intact=intact,
                                 hints=hints, strict_rule=strict, cap=cap)

    a1, a2, both = run(True, False), run(False, True), run(True, True)
    return {
        "case": name, "photos": len(photos), "zone_intact": intact,
        "a1": rows(a1.hints, a1, by_label),
        "a2": rows(a2.intact, a2, by_label),
        "both": rows(both.intact + both.hints, both, by_label),
        "a2_overrides": [{**o, "photo": _label(o["photo"])} for o in sorter_hints.overridden(intact, photos)],
        "skipped": {"a1": dict(sorted(a1.skipped.items())), "a2": dict(sorted(a2.skipped.items())),
                    "both": dict(sorted(both.skipped.items()))},
    }


def replay(paths: list[Path], cap: int = sorter_hints.MAX_HINTS) -> dict:
    cases = []
    for path in paths:
        trail = json.loads(Path(path).read_text(encoding="utf-8"))
        trails = trail if isinstance(trail, list) else [trail]
        for n, one in enumerate(trails):
            name = str(one.get("case") or (Path(path).stem if len(trails) == 1 else f"{Path(path).stem}#{n + 1}"))
            cases.append(replay_case(one, name, cap))
    totals = {key: sum(len(c[key]) for c in cases) for key in ("a1", "a2", "both", "a2_overrides")}
    return {"tool": "replay_sorter_hints", "version": VERSION, "cap": cap, "cases": cases, "totals": totals}


def table(result: dict) -> list[str]:
    lines = [f"{'case':<28} {'photos':>6} {'A1':>4} {'A2':>4} {'both':>5} {'overrides':>9}"]
    for c in result["cases"]:
        lines.append(f"{c['case'][:28]:<28} {c['photos']:>6} {len(c['a1']):>4} {len(c['a2']):>4} {len(c['both']):>5} "
                     f"{len(c['a2_overrides']):>9}")
        for row in c["both"]:
            lines.append(f"    + {row['part']} ({row['photo']}, {row['via']}{', intact' if row['intact_in'] else ''}"
                         f"{', read' if row['read'] else ''})")
    t = result["totals"]
    lines.append(f"{'total':<28} {'':>6} {t['a1']:>4} {t['a2']:>4} {t['both']:>5} {t['a2_overrides']:>9}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="replay_sorter_hints.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("trails", type=Path, nargs="+", help="trail JSON files, one case each (or a list of cases)")
    ap.add_argument("--out", type=Path, help="write the replay's JSON here")
    ap.add_argument("--cap", type=int, default=sorter_hints.MAX_HINTS, help="A1's cap a case (default 6)")
    args = ap.parse_args(argv)
    install_guard()
    if args.cap < 0:
        ap.error("--cap: 0 or more")
    result = replay(args.trails, args.cap)
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print("\n".join(table(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
