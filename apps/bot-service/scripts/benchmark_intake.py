"""Scores the intake AI against real surveyor reports.

Runs the real pipeline -- sorting, then reading -- on whole example cases
with real Gemini calls, and compares the drafted damage list and vehicle
facts with what the surveyor actually wrote (an answer key built from each
case's report, kept out of git because it holds real plates and chassis
numbers). Use it before and after changing photo selection, prompts or the
parts vocabulary, so changes are judged on numbers rather than one example.

It writes to whatever DATABASE_URL / STORAGE_DIR point at, so it refuses to
run unless BENCHMARK_SCRATCH=1 -- run it in a throwaway container or venv
with both pointing at scratch locations, never against the live database:

    BENCHMARK_SCRATCH=1 DATABASE_URL=sqlite:////bench/run.db STORAGE_DIR=/bench/storage \\
        python scripts/benchmark_intake.py --cases /bench/cases --key /bench/key.json \\
        --out /bench/out/baseline.json [--only "<case name>"] [--repeat 3] \\
        [--sort-cache /bench/sortcache] [--reports /bench/reports] [--label NAME --ref GIT_REF]

Gemini answers differ from run to run, so a single run cannot tell a real
gain of +3 parts from noise: --repeat N runs every case N times and reports
mean, min and max. --sort-cache freezes the sorting step (each photo's
result kept by its content hash), so a change to reading is measured without
sorting's variance. --reports scores the drafted conditions, and the Annex A
that prefill builds from the drafted parts, against each case's own surveyor
report, with the other reports as the firm's history (leave-one-out).

The reading is followed by the second look (app/ai/second_look.py), as in
the app: its suggestions are scored as if every one were ticked -- parts
found then, parts it added, its extras -- with its calls and models apart
from the reading's, so it ships only if it finds more beyond the noise for
few extras. --second-look-models measures another chain; --no-second-look
leaves it out.

The model settings (app/config.py, all unset by default) are set for a run
with --thinking-level-read / -sort / -second-look, --image-resolution and
--read-tools; the result file keeps them under "ai_settings", and every
Gemini call's tokens (prompt, output, thinking) and seconds under each
run's "ai_calls".

Contract 8 D2: --read-mode zones reads the car one area at a time after the
overall reading (intake/zones.py), --grid-marks draws the grid on the zone
calls' photos. The zone calls are their own stage ("draft_zone": calls,
tokens, seconds), and each run keeps what every zone sent and saw under
"zones".

Contract 8 D1 (the light reader looking closer): --read-tools takes the
per-model form ("gemini-3.1-flash-lite=code_execution"), with
--read-tools-timeout SECONDS for a try that carries the tool and
--zoom-hint asking the model to zoom (extraction.ZOOM_NOTE); code runs and
the tools' tokens are in the accounting. --sort-damage-box has the sorter
box the damage (its own sort-cache folder), and --read-crops [N] sends a
close-up of each box beside its photo, N at most (default the setting's 8);
each call notes the close-ups it sent ("crops").

Contract 9: --zone-models CHAIN sets the zone calls' own chain
(settings.gemini_zone_chain, the two lites by default; never the reading's),
--zone-only suggest|list says where the parts only zone reads found go
(option B: suggestions, scored as the second look's are, "if all ticked";
list: the damage list, as m5/m6), and --store-on sends the requests without
store=False, as before contract 9 -- for an A/B only. Each is in
ai_settings while it acts.

Contract 9, the evidence and the re-check: --zone-evidence reason|reason_box
asks the zone reads for each find's reason and box (settings.
intake_zone_evidence; needs --read-mode zones), and --check decision|lite|
decision_then_lite re-checks the suggestions after the second look
(settings.intake_check_mode, app/ai/check.py), tuned by --decision-model,
--check-models, --thinking-level-check, --check-resolution and the cut-offs
--check-yes / --check-no (each needs --check). Every run keeps its
suggestions in detail ("suggestions": part, reads, photos by case label,
evidence boxes and reasons, the re-check's verdict and p, and "truth": real,
wrong_side or extra against the key) and the re-check's step
(second_look.check); the metrics add the box and reason fill of the
suggestions and of the list, the suggestions real and wrong, the re-check's
verdicts on each, its calls and seconds, the 503s beside the 429s, and the
summary the calls per model. scripts/bench/replay_check.py runs the
re-check alone on a stored result's suggestions; scripts/bench/
probe_boxes.py draws its evidence boxes for a person to judge.

Contract 10, the linked parts: --links shadow|on works them out after the
reading (intake/links.py on the frozen table, its knobs at their defaults),
--link-check lite has the linked check look at them, --links-catalogue adds
the parts catalogue's diagram neighbours. A run then keeps them apart from
the other suggestions ("link_suggestions": part, kind, bucket, hit, hidden,
the parts they hang off, the table's rule, the linked check's answer, and
"truth") and scores them (score_links, as scripts/bench/replay_links.py
scores a stored run offline): real when a key part the list and the other
suggestions missed, the recall they add of all the key's parts and of the
inside ones, by kind, bucket and hit; ai_settings records the table's
version and sha256. --keep-reads keeps the overall reading's raw answer and
each zone read's items as merged (wrappers in this harness only). Without
these flags a run writes what it wrote before them. scripts/bench/gate.py
--suggestions passes or fails the linked parts by the plan's G1-G6.

Light-model detection (plans/2026-11-18-light-detection/P-prompts.md
section 4), one flag per experiment: --read-packs orient (R1) / cues,small
(R2), --read-prompt v2 (R3), --read-anchor (R4), --zone-packs small (Z1) /
intact (Z2) / orient,cues (Z3), --zone-photo-half (Z5), --sort-prompt v3
(S2; S1 is --thinking-level-sort high), --thinking-level-link high (L1),
--link-skill L2 with --link-prompt v2 (L2), --link-signs (L3),
--link-order photos_first (L4). Each is in ai_settings while it acts, with
the packs' sha256; without them a run writes what it wrote before them.

The assembly walk (plans/2026-11-18-car-knowledge/K1-design.md 5a):
--assembly-walk (needs --read-mode zones and a car map with signs,
app/intake/car_map.json) has each zone read answer the map's yes/no signs
about its damaged anchor panels (+0 calls); --walk-raise one|full and
--link-hops 1|2 (both need --assembly-walk and --links on|shadow) tune what
the linked parts make of them. While on: ai_settings has them with the
map's version and sha256, a run keeps the merged walk ("assembly_walk",
photos by label) and each linked part's hop / via / signs_yes.

Next to the strict parts score, "parts_fair" gives half a part for a
neighbouring panel on the same side, does not count an "Other / Not Listed"
item named like a key part's surveyor line as extra, and flags drafted
parts the surveyor passed as Serviceable (score_fair). --rescore FILE
scores a stored result file again with the current scoring, offline:

    python scripts/benchmark_intake.py --rescore out/a1-3x.json --key key.json [--reports DIR] [--out rescored.json]

Each run also gives the detail of the parts found (contract 8): the side
each was drafted on (part_sides: right, wrong, undetermined; inferred when
the pipeline placed it from the sorter's areas), Replace or Repair against
the surveyor's (part_remarks, with --reports), and found / missed by where
each key part sits on the car (part_zones: front, rear, left, right,
other). With --reports the Annex A score also sets its prices against the
surveyor's (score_prices). --rescore scores the detail again, and with
--reports and a scratch database (--scratch DIR) builds Annex A again,
prices included. scripts/bench/gate.py passes or fails a candidate result
file against a baseline by the owner's rule.

On the server, scripts/bench/run_bench.sh runs this inside the live api
container and scripts/bench/compare.py compares two result files; see
scripts/bench/README.md, which also has the commands for a run on a PC
(--scratch DIR puts the database and storage in a throwaway folder, and
BENCH_CASES / BENCH_KEY / BENCH_REPORTS / BENCH_SORT_CACHE give the paths).

Nothing from app is imported when this file loads: the offline tests
(scripts/test_benchmark_scoring.py) and compare.py import the scoring
functions. The app is imported by main() after the BENCHMARK_SCRATCH check,
because app.reports.db binds to DATABASE_URL the moment it is imported.
"""
import argparse
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import statistics
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

BOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BOT_DIR))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
REPORT_EXTENSIONS = {".xls", ".xlsx"}
SIDE_UNDETERMINED = " (side undetermined)"
# app.parts.history.FITTING_CONDITIONS: a line written for a part renewed on
# refit or kept, not damaged. Repeated (not imported) so scoring loads
# without the app; test_benchmark_scoring checks that the two agree.
FITTING_CONDITIONS = frozenset({"necessary", "serviceable"})
# What sorting writes on each photo (pipeline._sort); the sort cache keeps
# exactly these.
SORT_FIELDS = ("kind", "area", "quality", "shows_damage", "part", "note")
REFUSAL = ("Refusing to run: give --scratch DIR, or set BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR "
           "pointing at scratch locations.")
OTHER_PART = "Other / Not Listed"

log = logging.getLogger("benchmark")


# ---------------------------------------------------------------- parts


def _family(part: str) -> str:
    for side in ("Left ", "Right "):
        if part.startswith(side):
            part = part[len(side):]
    return part.removesuffix(SIDE_UNDETERMINED)


def covers(name: str, key_part: str) -> bool:
    """Whether a drafted or sorter part name stands for this key part: the
    same name, or a side-undetermined name of its family ("Door (side
    undetermined)" for "Left Front Door") -- score_parts' rule."""
    if name == key_part:
        return True
    return name.endswith(SIDE_UNDETERMINED) and _family(key_part).endswith(_family(name))


def score_parts(expected: list[dict], drafted: list[str]) -> dict:
    """Matches drafted part names to the key: exact name first, then a
    side-undetermined name against a same-family part (counted as found),
    then a same-family part on the wrong side (reported, not counted).
    `matched` says which drafted name each found key part came from, for
    the condition scoring."""
    drafted = [p for p in drafted if p]
    remaining = [e["part"] for e in expected]
    found: dict[str, str] = {}
    matched: dict[str, str] = {}
    wrong_side: list[list[str]] = []
    extra: list[str] = []
    leftover = []
    for part in drafted:
        if part in remaining:
            remaining.remove(part)
            found[part] = "exact"
            matched[part] = part
        else:
            leftover.append(part)
    for part in leftover:
        family = _family(part)
        if part.endswith(SIDE_UNDETERMINED):
            match = next((e for e in remaining if covers(part, e)), None)
            if match:
                remaining.remove(match)
                found[match] = f"as '{part}'"
                matched[match] = part
                continue
        elif part.startswith(("Left ", "Right ")):
            match = next((e for e in remaining if _family(e) == family), None)
            if match:
                remaining.remove(match)
                wrong_side.append([part, match])
                continue
        if part in found:  # the same part listed twice
            continue
        extra.append(part)
    hidden = {e["part"] for e in expected if e.get("hidden")}
    visible_total = len(expected) - len(hidden)
    return {
        "expected": len(expected),
        "found": len(found),
        "visible_expected": visible_total,
        "visible_found": len([p for p in found if p not in hidden]),
        "found_parts": found,
        "matched": matched,
        "missed": [p for p in remaining],
        "missed_visible": [p for p in remaining if p not in hidden],
        "wrong_side": wrong_side,
        "extra": extra,
        "side_undetermined": [p for p in drafted if p.endswith(SIDE_UNDETERMINED)],
    }


def score_second_look(expected: list[dict], parts: dict, drafted: list[str], suggested: list[str]) -> dict:
    """The second look's suggestions as if a person ticked every one. parts:
    score_parts of the reading alone. found / visible_found: the key parts
    found by the reading and the suggestions together. added: key parts the
    reading missed that a suggestion found (scored against the missed parts
    only, so a suggestion can't take a part the reading already had).
    extra: suggestions that match no missed key part; wrong_side: ones that
    name a missed part's family on the other side -- both cost the surveyor
    an untick."""
    suggested = [p for p in suggested if p]
    with_look = score_parts(expected, list(drafted) + suggested)
    missed = [e for e in expected if e["part"] in parts["missed"]]
    gain = score_parts(missed, suggested)
    return {
        "suggested": len(suggested),
        "found": with_look["found"],
        "visible_found": with_look["visible_found"],
        "added": gain["found"],
        "added_visible": gain["visible_found"],
        "added_parts": list(gain["found_parts"]),
        "extra": len(gain["extra"]),
        "extra_parts": gain["extra"],
        "wrong_side": len(gain["wrong_side"]),
    }


def classify_suggestions(expected: list[dict], parts: dict, suggested: list[str]) -> list[str]:
    """Each suggestion against the key, in order (contract 9): "real" -- it
    finds a key part the list missed (score_second_look's added) --,
    "wrong_side" -- that part's family on the other side --, or "extra":
    the names score_second_look counts."""
    missed = [e for e in expected if e["part"] in parts["missed"]]
    gain = score_parts(missed, [p for p in suggested if p])
    real = Counter(gain["matched"].values())
    wrong = Counter(part for part, _ in gain["wrong_side"])
    out = []
    for part in suggested:
        if part and real[part] > 0:
            real[part] -= 1
            out.append("real")
        elif part and wrong[part] > 0:
            wrong[part] -= 1
            out.append("wrong_side")
        else:
            out.append("extra")
    return out


def mark_truth(rows: list[dict], expected: list[dict], parts: dict) -> None:
    """Sets each suggestion row's "truth" (classify_suggestions), in place."""
    for row, truth in zip(rows, classify_suggestions(expected, parts, [r.get("part") for r in rows])):
        row["truth"] = truth


# ---------------------------------------------------------------- linked parts (contract 10)

# A linked part's kinds, buckets and hit levels (config.LINK_TYPES,
# intake/links.BUCKETS, config.HIT_LEVELS). Repeated (not imported) so
# scoring loads without the app; test_benchmark_scoring checks them against
# the app's. Each group's metrics have their own prefix: "crush" is a kind
# of link and a hit level.
LINK_TYPES = ("attached", "opens_with", "behind", "crush")
LINK_BUCKETS = ("seen", "likely", "strip_down")
HIT_LEVELS = ("light", "medium", "heavy", "crush")
LINK_GROUPS = (("link_type", "lt", LINK_TYPES), ("bucket", "lb", LINK_BUCKETS), ("hit", "lh", HIT_LEVELS))
LINK_TRUTHS = ("real", "wrong_side", "extra")
LINK_SIDE = "{side}"


def hidden_children(table) -> set[str]:
    """The parts a link table names as a hidden child (behind a part, inside
    it, on the crush path), "{side}" as Left and Right: with the key's own
    hidden parts, the "inside" parts the linked parts are for. Empty for no
    table."""
    out: set[str] = set()
    links = table.get("links") if isinstance(table, dict) else None
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict) or not link.get("hidden") or not isinstance(link.get("child"), str):
            continue
        child = link["child"]
        out.update([child.replace(LINK_SIDE, side) for side in ("Left", "Right")] if LINK_SIDE in child else [child])
    return out


def inside_parts(expected: list[dict], hidden=()) -> list[str]:
    """The key's parts a photo rarely shows, in key order: those the key
    marks hidden, and those in `hidden` (hidden_children of the table)."""
    hidden = set(hidden)
    return [e["part"] for e in expected if e.get("hidden") or e["part"] in hidden]


def score_links(expected: list[dict], drafted: list[str], zone_suggested: list[str], rows: list[dict],
                inside=()) -> dict:
    """Contract 10: the linked parts (rows, in the order shown; each sets its
    "truth" in place) against the key, with the strict score. A link is
    real when it finds a key part the list AND the other suggestions (zone
    reads', second look's) missed -- classify_suggestions against
    score_parts of list + suggestions --, wrong_side when it names such a
    part's family on the other side, else extra. Recall of the list alone,
    list + suggestions (lz) and list + suggestions + links (lzl), as if
    every suggestion were ticked: of all the key's parts, of its inside
    parts (inside_parts: the key's hidden ones and the table's hidden
    children) and of the key's hidden ones alone. By kind of link, bucket
    and hit level: shown, real, wrong_side, extra. strip_down_wrong: the
    wrong ones in the strip-down bucket (the gate's flood check)."""
    drafted = [p for p in drafted if p]
    zone = [p for p in zone_suggested if p]
    linked = [r.get("part") for r in rows]
    listed = score_parts(expected, drafted)
    lz = score_parts(expected, drafted + zone)
    truths = classify_suggestions(expected, lz, linked)
    for row, truth in zip(rows, truths):
        row["truth"] = truth
    lzl = score_parts(expected, drafted + zone + [p for p in linked if p])
    inside = inside_parts(expected, inside)
    hidden =[e["part"] for e in expected if e.get("hidden")]

    def found(score: dict, among: list[str]) -> int:
        return sum(1 for p in among if p in score["found_parts"])

    out = {
        "shown": len(rows),
        "real": truths.count("real"),
        "wrong_side": truths.count("wrong_side"),
        "extra": truths.count("extra"),
        "wrong": truths.count("wrong_side") + truths.count("extra"),
        "wrong_per_real": _ratio(truths.count("wrong_side") + truths.count("extra"), truths.count("real")),
        "real_parts": [p for p, t in zip(linked, truths) if t == "real"],
        "wrong_side_parts": [p for p, t in zip(linked, truths) if t == "wrong_side"],
        "extra_parts": [p for p, t in zip(linked, truths) if t == "extra"],
        "strip_down_wrong": sum(1 for r, t in zip(rows, truths) if r.get("bucket") == "strip_down" and t != "real"),
        "hidden_shown": sum(1 for r in rows if r.get("hidden")),
        "expected": len(expected),
        "list_found": listed["found"],
        "lz_found": lz["found"],
        "lzl_found": lzl["found"],
        "inside_expected": len(inside),
        "list_inside_found": found(listed, inside),
        "lz_inside_found": found(lz, inside),
        "lzl_inside_found": found(lzl, inside),
        "key_hidden_expected": len(hidden),
        "lz_hidden_found": found(lz, hidden),
        "lzl_hidden_found": found(lzl, hidden),
        "inside_parts": inside,
        "missed_with_links": list(lzl["missed"]),
    }
    for field, _, names in LINK_GROUPS:
        group = {name: dict.fromkeys(("shown",) + LINK_TRUTHS, 0) for name in names}
        for row, truth in zip(rows, truths):
            if row.get(field) in group:
                group[row[field]]["shown"] += 1
                group[row[field]][truth] += 1
        out[f"by_{field}"] = group
    return out


def link_metrics(block) -> dict:
    """A run's linked parts (score_links) as flat numbers for run_metrics,
    the summary and compare.py; {} for a run without them."""
    if not isinstance(block, dict) or "shown" not in block:
        return {}
    m = {"link_shown": block["shown"], "link_real": block["real"], "link_wrong_side": block["wrong_side"],
         "link_extra": block["extra"], "link_wrong": block["wrong"], "link_strip_wrong": block["strip_down_wrong"],
         "link_hidden_shown": block["hidden_shown"], "link_expected": block["expected"],
         "lz_found": block["lz_found"], "lzl_found": block["lzl_found"],
         "link_inside_expected": block["inside_expected"], "lz_inside_found": block["lz_inside_found"],
         "lzl_inside_found": block["lzl_inside_found"], "link_key_hidden_expected": block["key_hidden_expected"],
         "lz_hidden_found": block["lz_hidden_found"], "lzl_hidden_found": block["lzl_hidden_found"]}
    for field, prefix, names in LINK_GROUPS:
        group = block.get(f"by_{field}") or {}
        for name in names:
            for count in ("shown",) + LINK_TRUTHS:
                m[f"{prefix}_{name}_{count}"] = (group.get(name) or {}).get(count, 0)
    return m


def has_ai_box(entry: dict) -> bool:
    """An item or a suggestion the AI boxed: an evidence entry with an AI
    box, or (a list item) its bbox_2d. A sorter's box doesn't count."""
    if any(isinstance(e, dict) and e.get("box") and e.get("box_kind") in ("ai", None)
           for e in entry.get("evidence") or []):
        return True
    return bool(entry.get("bbox_2d"))


def has_box(entry: dict, kind: str | None = None) -> bool:
    """An evidence box of that kind ("ai", "sort"), or of any kind."""
    return any(isinstance(e, dict) and e.get("box") and (kind is None or (e.get("box_kind") or "ai") == kind)
               for e in entry.get("evidence") or [])


def has_reason(entry: dict) -> bool:
    return bool(entry.get("reason")) or any(isinstance(e, dict) and e.get("reason") for e in entry.get("evidence") or [])


VERDICTS = ("yes", "unsure", "no", "none")


def evidence_metrics(run: dict) -> dict:
    """Contract 9: the suggestions' evidence, against the key, and the
    re-check's verdicts on the real and the wrong ones; the list's box and
    reason fill. Nothing from a file written before them."""
    m: dict = {}
    sugg = run.get("suggestions")
    if sugg is not None:
        # Box fill split by who drew the box (the AI's, the sorter's), and
        # either: R2's gate reads them.
        m.update(sug_n=len(sugg), sug_box=sum(1 for s in sugg if has_ai_box(s)),
                 sug_sort_box=sum(1 for s in sugg if has_box(s, "sort")),
                 sug_any_box=sum(1 for s in sugg if has_box(s)),
                 sug_reason=sum(1 for s in sugg if has_reason(s)))
        verdicts = [((s.get("check") or {}).get("verdict") or "none") for s in sugg]
        checked = any(v != "none" for v in verdicts) or bool((run.get("second_look") or {}).get("check"))
        if checked:
            m["sug_checked"] = sum(1 for v in verdicts if v != "none")
        if all(s.get("truth") for s in sugg):
            m.update(sug_real=sum(1 for s in sugg if s["truth"] == "real"),
                     sug_wrong=sum(1 for s in sugg if s["truth"] != "real"))
            if checked:
                for group in ("real", "wrong"):
                    for v in VERDICTS:
                        m[f"chk_{group}_{v}"] = 0
                for s, v in zip(sugg, verdicts):
                    m[f"chk_{'real' if s['truth'] == 'real' else 'wrong'}_{v}"] += 1
                m["chk_real_kept"] = m["chk_real_yes"] + m["chk_real_unsure"]
    items = run.get("drafted_items") or []
    if any("evidence" in i or "bbox_2d" in i for i in items):
        m.update(list_n=len(items), list_box=sum(1 for i in items if has_ai_box(i)),
                 list_reason=sum(1 for i in items if has_reason(i)))
    look = run.get("second_look") or {}
    check = look.get("check")
    if isinstance(check, dict) and isinstance(check.get("seconds"), (int, float)):
        m["recheck_s"] = check["seconds"]
    return m


# ---------------------------------------------------------------- the fair score

# Panels that meet along one side of the car (contract 6). A reader that
# names the panel next to the damaged one has seen the damage and put it on
# the wrong side of a shut line: half a part in the fair score, not an
# extra. Names without the side, as taxonomy writes them.
NEIGHBOURS = (
    ("Front Fender", "Front Door"), ("Front Door", "Rear Door"), ("Rear Door", "Rear Quarter Panel"),
    ("Sill / Rocker Panel", "Front Door"), ("Sill / Rocker Panel", "Rear Door"),
    ("Front Wheel Arch Liner", "Front Fender"), ("Front Wheel Arch Trim", "Front Fender"),
    ("Rear Wheel Arch Liner", "Rear Quarter Panel"), ("Rear Wheel Arch Trim", "Rear Quarter Panel"),
    ("Front Wheel Arch Liner", "Front Wheel"), ("Rear Wheel Arch Liner", "Rear Wheel"),
    # Parts contract 6 splits out of one the key was written with (the arch
    # trim from the liner, the tyre from the wheel): the key still has the
    # old name, so the new one is its neighbour rather than an extra.
    ("Front Wheel Arch Liner", "Front Wheel Arch Trim"), ("Rear Wheel Arch Liner", "Rear Wheel Arch Trim"),
    ("Front Wheel", "Front Tyre"), ("Rear Wheel", "Rear Tyre"),
)
_NEXT_TO: dict[str, set[str]] = {}
for _a, _b in NEIGHBOURS:
    _NEXT_TO.setdefault(_a, set()).add(_b)
    _NEXT_TO.setdefault(_b, set()).add(_a)
# Words that place a part rather than name it: an "Other" item sharing
# "front" with a surveyor's line says nothing about which part it is.
_PLACE_WORDS = frozenset({"front", "right", "upper", "lower", "inner", "outer", "centre", "center", "nearside", "offside"})
# Where an "Other / Not Listed" item says what it is: other_part_name (the
# field contract 6 adds), and a description should one ever be kept.
_OTHER_TEXT_FIELDS = ("other_part_name", "description")


def _side_and_place(part: str) -> tuple[str | None, str]:
    for side in ("Left", "Right"):
        if part.startswith(side + " "):
            return side, part[len(side) + 1:]
    return None, part


def neighbours(a: str, b: str) -> bool:
    """Whether two part names are panels that meet (NEIGHBOURS) on the same
    side of the car. A "(side undetermined)" name has no side: never."""
    side_a, place_a = _side_and_place(a)
    side_b, place_b = _side_and_place(b)
    return side_a is not None and side_a == side_b and place_b in _NEXT_TO.get(place_a, ())


def _long_words(text) -> set[str]:
    return {w for w in words(text) if len(w) >= 5 and w not in _PLACE_WORDS}


def _end(text: str) -> str | None:
    t = f" {str(text or '').casefold()} "
    front, rear = " front " in t, " rear " in t
    return "front" if front and not rear else "rear" if rear and not front else None


def contradicted_parts(drafted: list[str], lines, family_of, side_of, part_family) -> list[list[str]]:
    """Drafted parts the surveyor looked at and passed: every line of the
    held-out report for the part (its family, history.family_of, on its side
    and at its end of the car) says Serviceable. key.json leaves such lines
    out, so these parts are extras -- ones the report contradicts. A part
    with no line at all is not flagged: nothing says it wasn't damaged.
    family_of / side_of / part_family: app.parts.history's family_of,
    side_of and family_and_side. Returns [part, a line's wording]."""
    out: list[list[str]] = []
    seen: set[str] = set()
    for part in drafted:
        if not part or part == OTHER_PART or part in seen:
            continue
        seen.add(part)
        family, side = part_family(part)
        end = _end(part)
        mine = []
        for line in lines:
            item = str(getattr(line, "item", "") or "")
            if family_of(item) != family:
                continue
            if side is not None and side_of(item) not in (None, side):
                continue
            if end is not None and _end(item) not in (None, end):
                continue
            mine.append(line)
        if mine and all(str(getattr(line, "condition", "") or "").strip().casefold() == "serviceable" for line in mine):
            out.append([part, mine[0].item])
    return out


def score_fair(expected: list[dict], drafted_items: list[dict], parts: dict | None = None,
               contradicted: list | None = None) -> dict:
    """The fair score, next to the strict one (score_parts, unchanged; its
    numbers are repeated here). Of the strict extras:

    - a part on the same side next to a key part the strict score missed
      (NEIGHBOURS) is a neighbour: half a part, not an extra. Each missed
      key part takes one neighbour, in drafted order;
    - an "Other / Not Listed" item whose name shares a word of 5+ letters
      (not a place word like "front") with a key part's surveyor wording is
      not an extra (and not found: it says nothing more about which part).

    contradicted: contradicted_parts of the drafted parts against the held-out
    report, or None when there is no report -- a flag; no number changes."""
    items = [i for i in drafted_items if isinstance(i, dict) and i.get("part")]
    strict = parts if parts is not None else score_parts(expected, [i["part"] for i in items])
    hidden = {e["part"] for e in expected if e.get("hidden")}
    missed = list(strict["missed"])
    neighbour: list[list[str]] = []
    left: list[str] = []
    for part in strict["extra"]:
        match = next((k for k in missed if neighbours(part, k)), None)
        if match:
            missed.remove(match)
            neighbour.append([part, match])
        else:
            left.append(part)
    wordings = [(e["part"], set().union(*[_long_words(w) for w in e.get("surveyor") or []])) for e in expected]
    other_named: list[list[str]] = []
    for item in (i for i in items if i["part"] == OTHER_PART):
        if OTHER_PART not in left:
            break
        text = " ".join(str(item.get(f) or "") for f in _OTHER_TEXT_FIELDS).strip()
        shared = _long_words(text)
        match = next((k for k, w in wordings if shared & w), None)
        if match:
            left.remove(OTHER_PART)
            other_named.append([text[:80], match])
    credit = 0.5 * len(neighbour)
    return {
        "expected": strict["expected"],
        "visible_expected": strict["visible_expected"],
        "found": strict["found"],
        "visible_found": strict["visible_found"],
        "fair_found": strict["found"] + credit,
        "fair_visible_found": strict["visible_found"] + 0.5 * sum(1 for _, k in neighbour if k not in hidden),
        "neighbour": neighbour,
        "other_named": other_named,
        "strict_extra": len(strict["extra"]),
        "extra": left,
        "fair_extra": len(left),
        "contradicted": contradicted,
    }


# ---------------------------------------------------------------- sides, Replace / Repair, zones


def score_sides(expected: list[dict], drafted_items: list[dict], parts: dict) -> dict:
    """The side of every key part with one (Left / Right in its name) that
    the strict score placed: right (drafted on its side), undetermined
    (found by a "(side undetermined)" name) or wrong (its family drafted on
    the other side: score_parts' wrong_side, not counted as found).
    inferred: the drafted name's side came from the sorter's photo areas
    (side_inferred, intake/pipeline.resolve_sides), not from the reader --
    so a wrong side tells which of the two put it there. Key parts without
    a side, and missed ones, are left out. parts: score_parts' result."""
    items = [i for i in drafted_items if isinstance(i, dict) and i.get("part")]
    # One item of a name read on its side is enough: the reader saw it there.
    inferred = {i["part"] for i in items if i.get("side_inferred")} - {i["part"] for i in items if not i.get("side_inferred")}
    wrong = {key: name for name, key in parts["wrong_side"]}
    details = []
    for entry in expected:
        key = entry["part"]
        if _side_and_place(key)[0] is None:
            continue
        name = parts["matched"].get(key)
        if name is not None:
            side = "undetermined" if name.endswith(SIDE_UNDETERMINED) else "right"
        elif key in wrong:
            name, side = wrong[key], "wrong"
        else:
            continue
        details.append({"part": key, "drafted_as": name, "side": side, "inferred": name in inferred})

    def count(side: str, inferred_only: bool = False) -> int:
        return sum(1 for d in details if d["side"] == side and (d["inferred"] or not inferred_only))

    return {
        "sided_found": len(details),
        "right": count("right"),
        "wrong": count("wrong"),
        "undetermined": count("undetermined"),
        "right_inferred": count("right", True),
        "wrong_inferred": count("wrong", True),
        "details": details,
    }


# taxonomy.FIRM_REMARKS: what the drafted remarks field may say.
REMARKS = ("Replace", "Repair")


def _remarks(value) -> str | None:
    text = str(value or "").strip().casefold()
    return next((r for r in REMARKS if r.casefold() == text), None)


def surveyor_remarks(part: dict, lines, normalise=None, side_of=None) -> str | None:
    """Whether the surveyor renewed or repaired a key part: the key's own
    "remarks" when it has one (key v2 has none), else the held-out
    report's. The firm writes a part it repairs rather than renews with the
    condition "Repair" (the line disallowed at 100%), while its Remarks
    column says "Replace" on those lines too, or is blank on a whole report
    -- so the condition of the line surveyor_condition picks decides:
    Repair, else Replace. None: no damage line for the part."""
    own = _remarks(part.get("remarks"))
    if own:
        return own
    surveyor = surveyor_condition(part, lines or [], normalise, side_of)
    if surveyor is None:
        return None
    return "Repair" if _squash(surveyor["condition"]) == "repair" else "Replace"


def score_remarks(expected: list[dict], matched: dict[str, str], drafted_items: list[dict], lines,
                  normalise=None, side_of=None) -> dict:
    """Replace or Repair on every key part found (score_parts' matched):
    the drafted item's remarks against the surveyor's (surveyor_remarks)
    -- agree, disagree, or unsure (the reader left it null, as its prompt
    asks when unsure). A part neither the key nor the report decides is
    not scored. The surveyor repairs few parts (3 of key v2's 73), so
    Repair is counted apart: always drafting Replace would agree on the
    rest."""
    by_part: dict[str, dict] = {}
    for item in drafted_items:
        by_part.setdefault(item.get("part"), item)
    details = []
    for entry in expected:
        name = matched.get(entry["part"])
        if name is None:
            continue
        want = surveyor_remarks(entry, lines, normalise, side_of)
        if want is None:
            continue
        got = _remarks((by_part.get(name) or {}).get("remarks"))
        verdict = "unsure" if got is None else "agree" if got == want else "disagree"
        details.append({"part": entry["part"], "drafted_as": name, "surveyor": want, "drafted": got, "verdict": verdict})
    repair = [d for d in details if d["surveyor"] == "Repair"]
    return {
        "scored": len(details),
        "agree": sum(1 for d in details if d["verdict"] == "agree"),
        "disagree": sum(1 for d in details if d["verdict"] == "disagree"),
        "unsure": sum(1 for d in details if d["verdict"] == "unsure"),
        "surveyor_repair": len(repair),
        "repair_agree": sum(1 for d in repair if d["verdict"] == "agree"),
        "details": details,
    }


# Where a part sits on the car, one zone each so the zones add up to the
# whole key: a part on the car's flank (second_look.ZONES' left or right)
# is that side's even at a corner -- a fender, a quarter panel and their
# arches are photographed from the side that was hit; any other part is at
# the end of the car it is on (a headlamp front, a tail lamp rear); a part
# at both ends or neither (a parking sensor, the roof, the underbody, the
# interior), and a flank part with no side, is "other". Repeated (not
# imported) so scoring loads without the app; test_benchmark_scoring
# checks it against second_look.ZONES.
ZONES = ("front", "rear", "left", "right", "other")
_FLANK = frozenset({
    "Front Fender", "Front Door", "Rear Door", "Front Door Glass", "Rear Door Glass", "Front Window Regulator",
    "Rear Window Regulator", "Wing Mirror", "Sill / Rocker Panel", "Rear Quarter Panel", "Front Wheel", "Rear Wheel",
    "Front Tyre", "Rear Tyre", "Front Wheel Arch Liner", "Rear Wheel Arch Liner", "Front Wheel Arch Trim",
    "Rear Wheel Arch Trim",
})
_FRONT_END = frozenset({
    "Front Bumper", "Front Bumper Beam", "Front Grille", "Bonnet", "Headlamp", "Fog Lamp", "Front Number Plate",
    "Front Windscreen", "Engine Undercover", "Radiator", "Air Conditioning Condenser", "Front Subframe",
})
_REAR_END = frozenset({
    "Rear Bumper", "Rear Bumper Beam", "Rear Bumper Reflector", "Boot Lid", "Boot Lid Lock", "Boot Lid Hinge",
    "Boot Lid Weatherstrip", "Boot Lid Inner Trim", "Boot Lid Lamp", "Tail Lamp", "Rear End Panel", "Rear Windscreen",
    "Rear Number Plate", "Boot Floor Board", "Boot Side Trim", "Rear Floor Panel", "Reversing Camera", "Exhaust System",
})


def part_zone(part: str) -> str:
    """front, rear, left, right or other: where the part sits (ZONES)."""
    side, place = _side_and_place(part)
    if side is not None and place in _FLANK:
        return side.lower()
    place = place.removesuffix(SIDE_UNDETERMINED)
    return "front" if place in _FRONT_END else "rear" if place in _REAR_END else "other"


def score_zones(expected: list[dict], parts: dict) -> dict:
    """score_parts by where each key part sits (part_zone): per zone the
    key parts expected, found, drafted on the wrong side and missed (named),
    and the extras by where they would sit. A zone's found + wrong_side +
    missed is its expected; the zones add up to score_parts' numbers."""
    wrong = {key for _, key in parts["wrong_side"]}
    out = {zone: {"expected": 0, "found": 0, "wrong_side": 0, "missed": 0, "extra": 0, "missed_parts": []}
           for zone in ZONES}
    for entry in expected:
        key = entry["part"]
        zone = out[part_zone(key)]
        zone["expected"] += 1
        if key in parts["found_parts"]:
            zone["found"] += 1
        elif key in wrong:
            zone["wrong_side"] += 1
        else:
            zone["missed"] += 1
            zone["missed_parts"].append(key)
    for name in parts["extra"]:
        out[part_zone(name)]["extra"] += 1
    return out


def score_details(run: dict, case: dict, lines=None, normalise=None, side_of=None) -> None:
    """The detail of a scored run's found parts, from its strict parts and
    drafted items: part_sides, part_zones and -- with the held-out report's
    lines, or a key that says -- part_remarks. run_case and --rescore."""
    items = run.get("drafted_items") or [{"part": p} for p in run.get("drafted_parts") or [] if p]
    run["part_sides"] = score_sides(case["parts"], items, run["parts"])
    run["part_zones"] = score_zones(case["parts"], run["parts"])
    if lines is not None or any(_remarks(e.get("remarks")) for e in case["parts"]):
        run["part_remarks"] = score_remarks(case["parts"], run["parts"]["matched"], items, lines, normalise, side_of)


def sorter_quality(expected: list[dict], photos: list[dict]) -> dict:
    """How useful sorting's per-photo part labels were. photos: the run's
    sorted_photos ({"part", "read", ...} in case order).

    label precision: photos tagged with a part, whose part is in the key;
    photographed recall: key parts at least one photo is tagged with;
    selection coverage: key-part photos that reached the reading call."""
    key_parts = [e["part"] for e in expected]
    hidden = {e["part"] for e in expected if e.get("hidden")}
    tagged = [p for p in photos if p.get("part")]
    in_key = [p for p in tagged if any(covers(p["part"], k) for k in key_parts)]
    photographed = [k for k in key_parts if any(covers(p["part"], k) for p in tagged)]
    parts_read = [k for k in photographed if any(covers(p["part"], k) and p.get("read") for p in tagged)]
    key_read = [p for p in in_key if p.get("read")]
    return {
        "tagged_photos": len(tagged),
        "tagged_in_key": len(in_key),
        "label_precision": _ratio(len(in_key), len(tagged)),
        "key_parts": len(key_parts),
        "key_parts_photographed": len(photographed),
        "visible_key_parts": len([k for k in key_parts if k not in hidden]),
        "visible_photographed": len([k for k in photographed if k not in hidden]),
        "photographed_recall": _ratio(len(photographed), len(key_parts)),
        "key_photos": len(in_key),
        "key_photos_read": len(key_read),
        "selection_coverage": _ratio(len(key_read), len(in_key)),
        "key_parts_read": len(parts_read),
        "not_photographed": [k for k in key_parts if k not in photographed],
        "photographed_not_read": [k for k in photographed if k not in parts_read],
        "tags_not_in_key": sorted({p["part"] for p in tagged} - {p["part"] for p in in_key}),
    }


# ---------------------------------------------------------------- vehicle facts

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.lower()
    if m := re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        y, mo, d = map(int, m.groups())
    elif m := re.search(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text):
        d, mo, y = map(int, m.groups())
    elif m := re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})", text):
        d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2)), int(m.group(3))
    else:
        return None
    try:
        return date(y, mo, d)
    except (TypeError, ValueError):
        return None


def _alnum(value) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def score_vehicle(expected: dict, drafted: dict) -> dict:
    checks = {
        "plate_number": lambda e, a: _alnum(e) == _alnum(a),
        "make": lambda e, a: e.lower() in str(a or "").lower(),
        "model": lambda e, a: e.lower() in str(a or "").lower(),
        "color": lambda e, a: e.lower() in str(a or "").lower(),
        "vin": lambda e, a: _alnum(e) == _alnum(a),
        "engine_number": lambda e, a: _alnum(e) == _alnum(a),
        "date_of_registration": lambda e, a: _parse_date(a) == date.fromisoformat(e),
    }
    result = {}
    for field, ok in checks.items():
        got = drafted.get(field)
        result[field] = {"expected": expected[field], "got": got,
                         "verdict": "missing" if got in (None, "") else ("ok" if ok(expected[field], got) else "WRONG")}
    return result


# ---------------------------------------------------------------- conditions


def _squash(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def words(text) -> list[str]:
    return re.findall(r"[a-z]+", str(text or "").casefold())


def condition_tokens(text) -> list[str]:
    """"Dented/buckled" -> ["dented", "buckled"]: the firm writes a part's
    condition as words joined by "/"."""
    return [" ".join(words(t)) for t in str(text or "").split("/") if words(t)]


def drafted_condition(item: dict) -> tuple[str | None, str | None]:
    """(value, field) of what a drafted item says about the part's state:
    `condition` (the firm's wording, once reading writes one), else
    damage_type -- the fallback prefill uses for the part's main row."""
    for field in ("condition", "damage_type"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip(), field
    return None, None


def surveyor_condition(part: dict, lines, normalise=None, side_of=None) -> dict | None:
    """The surveyor's condition for one key part: the first Annex A line of
    the held-out report (in its order) whose wording is one of the part's
    `surveyor` wordings and whose condition is a damage -- not blank, not
    Necessary or Serviceable. The key writes some wordings with a count the
    sheet doesn't have ("Headlamps (x2)"), so a wording matching no line
    exactly is matched by normalise (history.normalise_item) on the same
    side (history.side_of), since normalise_item drops LH/RH."""
    wordings = [w for w in part.get("surveyor") or [] if w]

    def damage(line) -> str | None:
        condition = str(getattr(line, "condition", "") or "").strip()
        return condition if condition and condition.casefold() not in FITTING_CONDITIONS else None

    exact = {_squash(w) for w in wordings}
    for line in lines:
        if _squash(line.item) in exact and damage(line):
            return {"item": line.item, "condition": damage(line)}
    if normalise and side_of:
        loose = {(normalise(w), side_of(w)) for w in wordings}
        for line in lines:
            if (normalise(line.item), side_of(line.item)) in loose and damage(line):
                return {"item": line.item, "condition": damage(line)}
    return None


def score_conditions(
    expected: list[dict],
    matched: dict[str, str],
    drafted_items: list[dict],
    lines,
    firm_words: set[str],
    normalise=None,
    side_of=None,
) -> dict:
    """The drafted condition of every key part found, against the
    surveyor's (surveyor_condition): exact (the same "/" words, any case),
    first word the same, and word recall (the surveyor's words present in
    the drafted value). Also the share of all drafted conditions that use
    only words the firm's reports use (firm_words)."""
    by_part: dict[str, dict] = {}
    for item in drafted_items:
        by_part.setdefault(item.get("part"), item)
    details = []
    for entry in expected:
        name = matched.get(entry["part"])
        if name is None:
            continue  # not found: there is no drafted condition to compare
        surveyor = surveyor_condition(entry, lines, normalise, side_of)
        value, source = drafted_condition(by_part.get(name) or {})
        row = {"part": entry["part"], "drafted_as": name, "surveyor": surveyor["condition"] if surveyor else None,
               "drafted": value, "source": source}
        if surveyor:
            want = condition_tokens(surveyor["condition"])
            got = condition_tokens(value)
            have = " " + " ".join(words(value)) + " "
            row["exact"] = bool(want) and want == got
            row["first_token"] = bool(want and got) and want[0] == got[0]
            row["token_hits"] = sum(1 for token in want if f" {token} " in have)
            row["tokens"] = len(want)
        details.append(row)
    scored = [d for d in details if d["surveyor"]]
    conditions = [drafted_condition(item)[0] for item in drafted_items]
    conditions = [c for c in conditions if c and words(c)]
    firm_only = [c for c in conditions if set(words(c)) <= firm_words]
    hits = sum(d["token_hits"] for d in scored)
    tokens = sum(d["tokens"] for d in scored)
    return {
        "found_parts": len(details),
        "scored": len(scored),
        "no_surveyor_condition": len(details) - len(scored),
        "exact": sum(1 for d in scored if d["exact"]),
        "first_token": sum(1 for d in scored if d["first_token"]),
        "token_hits": hits,
        "tokens": tokens,
        "token_recall": _ratio(hits, tokens),
        "drafted_conditions": len(conditions),
        "firm_words_only": len(firm_only),
        "firm_words_share": _ratio(len(firm_only), len(conditions)),
        "words_not_in_firm_reports": sorted({w for c in conditions for w in words(c) if w not in firm_words}),
        "details": details,
    }


# ---------------------------------------------------------------- summary

# (name, numerator, denominator). Recomputed from the summed counts for a
# total, never averaged over cases: a 36-part case would otherwise weigh
# the same as a five-part one.
RATIOS = (
    ("visible_recall", "visible_found", "visible_expected"),
    ("label_precision", "sorter_tagged_in_key", "sorter_tagged"),
    ("photographed_recall", "key_parts_photographed", "expected"),
    ("selection_coverage", "key_photos_read", "key_photos"),
    ("cond_exact_rate", "cond_exact", "cond_scored"),
    ("cond_first_token_rate", "cond_first_token", "cond_scored"),
    ("cond_token_recall", "cond_token_hits", "cond_tokens"),
    ("cond_firm_share", "cond_firm_only", "cond_drafted"),
    ("annex_coverage", "annex_covered", "annex_lines"),
    ("annex_perfect_coverage", "annex_perfect_covered", "annex_lines"),
    ("side_right_rate", "side_right", "sided_found"),
    ("remarks_agree_rate", "remarks_agree", "remarks_scored"),
    ("annex_within_10_rate", "annex_within_10", "annex_covered"),
    # Signed: -0.8 is an Annex A priced at a fifth of the surveyor's.
    ("annex_price_diff_rate", "annex_price_diff", "annex_report_price_total"),
) + tuple((f"zone_{zone}_recall", f"zone_{zone}_found", f"zone_{zone}_expected") for zone in ZONES)
# Contract 9: the evidence's fill, and the re-check: the real suggestions
# it kept (yes or unsure), the wrong ones it called "no", and how many it
# answered at all.
RATIOS += (
    ("sug_box_fill", "sug_box", "sug_n"),
    ("sug_any_box_fill", "sug_any_box", "sug_n"),
    ("sug_reason_fill", "sug_reason", "sug_n"),
    ("list_box_fill", "list_box", "list_n"),
    ("list_reason_fill", "list_reason", "list_n"),
    ("chk_real_kept_rate", "chk_real_kept", "sug_real"),
    ("chk_wrong_no_rate", "chk_wrong_no", "sug_wrong"),
    ("sug_checked_rate", "sug_checked", "sug_n"),
)
# Contract 10: the linked parts' wrong per real, and the recall of the list
# + the other suggestions (lz), + the links (lzl), as if all were ticked --
# of every key part, of the inside ones and of the key's hidden ones.
RATIOS += (
    ("link_wrong_per_real", "link_wrong", "link_real"),
    ("lz_recall", "lz_found", "link_expected"),
    ("lzl_recall", "lzl_found", "link_expected"),
    ("lz_inside_recall", "lz_inside_found", "link_inside_expected"),
    ("lzl_inside_recall", "lzl_inside_found", "link_inside_expected"),
    ("lzl_hidden_recall", "lzl_hidden_found", "link_key_hidden_expected"),
)
_RATIO_NAMES = {name for name, _, _ in RATIOS}


def _ratio(num, den) -> float | None:
    return round(num / den, 3) if den else None


def run_ok(run: dict) -> bool:
    """A run that scored: an error (quota, a crash) says nothing about
    detection, so it is counted apart rather than averaged in as zeros."""
    return "parts" in run and not run.get("error")


def run_metrics(run: dict) -> dict:
    """One run as flat numbers. Works on the files written before --repeat
    ({"results": [...]}) too, so compare.py can set a new run against the
    2026-11-05 baseline."""
    if "parts" not in run:
        return {}
    p = run["parts"]
    m = {
        "found": p["found"],
        "expected": p["expected"],
        "visible_found": p["visible_found"],
        "visible_expected": p["visible_expected"],
        "extra": len(p["extra"]),
        "wrong_side": len(p["wrong_side"]),
        "side_undetermined": sum(1 for d in run.get("drafted_parts") or [] if d and d.endswith(SIDE_UNDETERMINED)),
        "facts_ok": sum(1 for x in (run.get("vehicle") or {}).values() if x.get("verdict") == "ok"),
        "photos_read": run.get("read"),
        "sort_s": run.get("sort_seconds"),
        "read_s": run.get("read_seconds"),
    }
    pc = run.get("parts_with_checklist")
    if pc:
        m.update(checklist_found=pc["found"], checklist_visible_found=pc["visible_found"], checklist_extra=len(pc["extra"]))
    fair = run.get("parts_fair")
    if fair:
        m.update(fair_found=fair["fair_found"], fair_visible_found=fair["fair_visible_found"], fair_extra=fair["fair_extra"],
                 neighbour=len(fair["neighbour"]), other_named=len(fair["other_named"]))
        if fair.get("contradicted") is not None:
            m["contradicted"] = len(fair["contradicted"])
    look = run.get("parts_with_second_look")
    if look:
        m.update(look_found=look["found"], look_visible_found=look["visible_found"], look_added=look["added"],
                 look_extra=look["extra"], look_wrong_side=look["wrong_side"], look_suggested=look["suggested"])
    info = run.get("second_look")
    if info:
        m.update(look_calls=info.get("calls"), look_s=info.get("seconds"), look_ok=1 if info.get("status") == "ok" else 0)
    accounting = run.get("accounting")
    if accounting is not None:
        m.update(
            calls=sum(s.get("calls", 0) for s in accounting.values()),
            attempts=sum(s.get("attempts", 0) for s in accounting.values()),
            n_429=sum(s.get("n_429", 0) for s in accounting.values()),
            wait_429_s=round(sum(s.get("wait_429_s", 0.0) for s in accounting.values()), 1),
            slot_wait_s=round(sum(s.get("slot_wait_s", 0.0) for s in accounting.values()), 1),
        )
        # Contract 9: Google's "busy" (503) apart from the other errors, in
        # the files that count them.
        if any("n_503" in s for s in accounting.values()):
            m["n_503"] = sum(s.get("n_503", 0) for s in accounting.values())
        # Tokens per kind of call, when the SDK reported them.
        for purpose, short in _SHORT_PURPOSES.items():
            stage = accounting.get(purpose) or {}
            for field, suffix in _TOKEN_METRICS:
                if stage.get(field) is not None:
                    m[f"{short}_{suffix}"] = stage[field]
        # Every kind of call's prompt tokens together: what a reading mode
        # costs, whatever its extra calls are named.
        prompt = [s["prompt_tokens"] for s in accounting.values() if s.get("prompt_tokens") is not None]
        if prompt:
            m["tok_in"] = sum(prompt)
    # Contract 8 D2: the zone stage -- its calls, failures and seconds; the
    # side zones whose photos showed the other side than the sorter's; and
    # the parts only a zone found, or both kinds of read.
    zone_stage = (accounting or {}).get("draft_zone")
    if zone_stage:
        m.update(zone_calls=zone_stage.get("calls"), zone_failed=zone_stage.get("failed_calls"),
                 zone_s=zone_stage.get("seconds"))
    # Contract 9: the re-check's Decisions API requests and its lite call;
    # contract 10: the linked check's (ai/link_check.PURPOSE "link").
    for purpose, short in (("decision", "dec"), ("check", "check"), ("link", "lchk")):
        stage = (accounting or {}).get(purpose)
        if stage:
            m.update({f"{short}_calls": stage.get("calls"), f"{short}_failed": stage.get("failed_calls"),
                      f"{short}_s": stage.get("seconds")})
    m.update(evidence_metrics(run))
    # Contract 10: the linked parts, in a run that had them on.
    m.update(link_metrics(run.get("links")))
    zones = (run.get("zones") or {}).get("zones")
    if zones is not None:
        m["zone_side_flips"] = sum(1 for z in zones if z.get("zone") in ("left", "right")
                                   and z.get("side_seen") in ("left", "right") and z.get("side_seen") != z.get("zone"))
        m["zone_other_end"] = sum(1 for z in zones if z.get("zone") in ("front", "rear")
                                  and z.get("side_seen") in ("front", "rear") and z.get("side_seen") != z.get("zone"))
    found_by = [i.get("found_by") for i in run.get("drafted_items") or [] if i.get("found_by")]
    if found_by:
        m["zone_only_parts"] = sum(1 for f in found_by if "overall" not in f)
        m["both_parts"] = sum(1 for f in found_by if "overall" in f and len(f) > 1)
    # The close-ups the reading sent (--read-crops).
    crops = sum(c.get("crops", 0) for c in run.get("ai_calls") or [] if c.get("purpose") == "draft" and c.get("ok"))
    if crops:
        m["read_crops"] = crops
    cache = run.get("sort_cache")
    if cache:
        m["sort_cache_hits"] = cache.get("hits", 0)
    sorter = run.get("sorter")
    if sorter is None and run.get("sorted_photos") is not None:
        # Older files: the key's parts are what was found plus what was missed.
        sorter = sorter_quality([{"part": k} for k in list(p["found_parts"]) + list(p["missed"])], run["sorted_photos"])
    if sorter:
        m.update(sorter_tagged=sorter["tagged_photos"], sorter_tagged_in_key=sorter["tagged_in_key"],
                 key_parts_photographed=sorter["key_parts_photographed"], key_photos=sorter["key_photos"],
                 key_photos_read=sorter["key_photos_read"])
    cond = run.get("conditions")
    if cond:
        m.update(cond_scored=cond["scored"], cond_exact=cond["exact"], cond_first_token=cond["first_token"],
                 cond_token_hits=cond["token_hits"], cond_tokens=cond["tokens"],
                 cond_drafted=cond["drafted_conditions"], cond_firm_only=cond["firm_words_only"])
    annex = run.get("annex_a")
    if annex:
        m.update(annex_lines=annex["real_lines"], annex_covered=annex["covered"], annex_rows=annex["rows"],
                 annex_priced=annex["priced"], annex_unpriced=annex["unpriced"], annex_extra=annex["extra"],
                 annex_canonical=annex["canonical_rows"], annex_perfect_covered=annex["perfect_covered"])
        # The prices (score_prices): absent from files written before them.
        m.update(annex_price_total=annex.get("price_total"), annex_report_price_total=annex.get("report_price_total"),
                 annex_price_diff=annex.get("price_diff"), annex_adjusted_diff=annex.get("adjusted_diff"),
                 annex_matched_priced=annex.get("matched_priced"), annex_within_10=annex.get("within_10pct"),
                 annex_price_abs_diff=annex.get("matched_abs_diff"),
                 annex_perfect_within_10=annex.get("perfect_within_10pct"))
    sides = run.get("part_sides")
    if sides:
        m.update(sided_found=sides["sided_found"], side_right=sides["right"], side_undetermined_found=sides["undetermined"],
                 side_right_inferred=sides["right_inferred"], side_wrong_inferred=sides["wrong_inferred"])
    remarks = run.get("part_remarks")
    if remarks:
        m.update(remarks_scored=remarks["scored"], remarks_agree=remarks["agree"], remarks_disagree=remarks["disagree"],
                 remarks_unsure=remarks["unsure"], remarks_repair=remarks["surveyor_repair"],
                 remarks_repair_agree=remarks["repair_agree"])
    zones = run.get("part_zones")
    if zones:
        for zone in ZONES:
            z = zones.get(zone) or {}
            m.update({f"zone_{zone}_expected": z.get("expected"), f"zone_{zone}_found": z.get("found"),
                      f"zone_{zone}_extra": z.get("extra")})
    if m.get("read_s") is not None:
        # The case's wall-clock seconds: read_s leaves the second look and
        # the re-check out.
        m["total_s"] = round(sum(m.get(k) or 0 for k in ("sort_s", "read_s", "look_s", "recheck_s")), 1)
    m = {k: v for k, v in m.items() if v is not None}
    return with_ratios(m)


def with_ratios(m: dict) -> dict:
    for name, num, den in RATIOS:
        if num in m and m.get(den):
            m[name] = _ratio(m[num], m[den])
    return m


def stats(values) -> dict | None:
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals:
        return None
    return {"mean": round(statistics.fmean(vals), 3), "min": min(vals), "max": max(vals), "n": len(vals)}


def _stats_table(rows: list[dict]) -> dict:
    names = list(dict.fromkeys(k for row in rows for k in row))
    return {k: stats([row[k] for row in rows if k in row]) for k in names}


def _sum_counts(rows: list[dict]) -> dict:
    total: dict = {}
    for row in rows:
        for k, v in row.items():
            if k not in _RATIO_NAMES:
                total[k] = round(total.get(k, 0) + v, 3)
    return with_ratios(total)


def summarize(runs: list[dict]) -> dict:
    """Mean, min and max of every metric per case (over its runs) and in
    total (over the repetitions in which every case scored, each one the
    sum over the cases)."""
    names = list(dict.fromkeys(r.get("case") for r in runs))
    reps = sorted({int(r.get("rep") or 1) for r in runs})
    cases = {}
    for name in names:
        mine = [r for r in runs if r.get("case") == name]
        rows = [run_metrics(r) for r in mine if run_ok(r)]
        cases[name] = {"runs": len(mine), "errors": len(mine) - len(rows), "metrics": _stats_table(rows)}
    totals, complete = [], []
    for rep in reps:
        mine = [r for r in runs if int(r.get("rep") or 1) == rep]
        if sorted(r.get("case") for r in mine) == sorted(names) and all(run_ok(r) for r in mine):
            totals.append(_sum_counts([run_metrics(r) for r in mine]))
            complete.append(rep)
    return {
        "cases_n": len(names),
        "reps": len(reps),
        "complete_reps": complete,
        "cases": cases,
        "total": _stats_table(totals),
        "errors": [{"case": r.get("case"), "rep": int(r.get("rep") or 1), "error": r.get("error")} for r in runs if not run_ok(r)],
        "accounting": merge_accounting([r["accounting"] for r in runs if r.get("accounting")]),
        # Contract 9: every kind of call together, per model.
        "by_model": model_calls(merge_accounting([r["accounting"] for r in runs if r.get("accounting")])),
        "sort_cache": {
            "hits": sum((r.get("sort_cache") or {}).get("hits", 0) for r in runs),
            "misses": sum((r.get("sort_cache") or {}).get("misses", 0) for r in runs),
        },
    }


# ---------------------------------------------------------------- AI accounting

# What call_structured reports per try beside its outcome (its
# _note_attempt), summed per call, kind of call and model when present --
# never a made-up 0 when the SDK said nothing.
TOKEN_FIELDS = ("prompt_tokens", "output_tokens", "thought_tokens", "tool_calls", "tool_tokens")
# accounting's purposes -> the short name in run metrics ("read_tok_in").
_SHORT_PURPOSES = {"draft": "read", "photo sort": "sort", "second look": "look"}
# Contract 8 D2: the zone calls (ai/zone_read.PURPOSE), "zone_tok_in"...
_SHORT_PURPOSES["draft_zone"] = "zone"
# Contract 9: the re-check -- the Decisions API's requests (ai/decisions.
# PURPOSE) and the lite call ("dec_tok_in", "check_tok_in"...).
_SHORT_PURPOSES.update({"decision": "dec", "check": "check"})
# Contract 10: the linked check (ai/link_check.PURPOSE): "lchk_tok_in"...
_SHORT_PURPOSES["link"] = "lchk"
_TOKEN_METRICS = (("prompt_tokens", "tok_in"), ("output_tokens", "tok_out"), ("thought_tokens", "tok_think"),
                  ("tool_calls", "code_runs"), ("tool_tokens", "tok_tool"))
# The label of a close-up's part (extraction._build_input, contract 8 D1):
# how a call's close-ups are counted.
CLOSE_UP_LABEL = "Close-up of photo "


def _add_tokens(into: dict, source: dict) -> None:
    for field in TOKEN_FIELDS:
        value = source.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            into[field] = into.get(field, 0) + value


def _empty_stage() -> dict:
    return {"calls": 0, "failed_calls": 0, "seconds": 0.0, "attempts": 0, "outcomes": {},
            "slot_wait_s": 0.0, "n_429": 0, "wait_429_s": 0.0, "n_503": 0, "by_model": {}}


def _empty_model() -> dict:
    return {"attempts": 0, "answered": 0, "seconds": 0.0, "outcomes": {}}


def accounting_summary(calls: list[dict], attempts: list[dict]) -> dict:
    """Per purpose ("photo sort", "draft", ...): calls and their wall-clock
    seconds; every model try, by outcome and by model (answered = it gave
    the answer used); seconds waited for a rate-limit slot, and 429s with
    the seconds waited on the delay Google asked for."""
    out: dict = {}
    for c in calls:
        s = out.setdefault(c["purpose"], _empty_stage())
        s["calls"] += 1
        s["failed_calls"] += 0 if c.get("ok") else 1
        s["seconds"] += c["seconds"]
    for a in attempts:
        s = out.setdefault(a["purpose"], _empty_stage())
        s["attempts"] += 1
        s["outcomes"][a["outcome"]] = s["outcomes"].get(a["outcome"], 0) + 1
        s["slot_wait_s"] += a["slot_wait_s"]
        s["wait_429_s"] += a["wait_429_s"]
        s["n_429"] += 1 if a["outcome"] == "429" else 0
        s["n_503"] += 1 if a.get("busy") else 0
        m = s["by_model"].setdefault(a["model"], _empty_model())
        m["attempts"] += 1
        m["answered"] += 1 if a["outcome"] == "ok" else 0
        if a.get("busy"):
            m["n_503"] = m.get("n_503", 0) + 1
        m["seconds"] += a["seconds"]
        m["outcomes"][a["outcome"]] = m["outcomes"].get(a["outcome"], 0) + 1
        _add_tokens(s, a)
        _add_tokens(m, a)
    return merge_accounting([out])  # the same rounding as a merge


def merge_accounting(summaries: list[dict]) -> dict:
    out: dict = {}
    for summary in summaries:
        for purpose, s in summary.items():
            t = out.setdefault(purpose, _empty_stage())
            for k in ("calls", "failed_calls", "attempts", "n_429", "n_503"):
                t[k] += s.get(k, 0)
            for k in ("seconds", "slot_wait_s", "wait_429_s"):
                t[k] = round(t[k] + s.get(k, 0.0), 1)
            for outcome, n in (s.get("outcomes") or {}).items():
                t["outcomes"][outcome] = t["outcomes"].get(outcome, 0) + n
            _add_tokens(t, s)
            for model, ms in (s.get("by_model") or {}).items():
                tm = t["by_model"].setdefault(model, _empty_model())
                tm["attempts"] += ms.get("attempts", 0)
                tm["answered"] += ms.get("answered", 0)
                tm["seconds"] = round(tm["seconds"] + ms.get("seconds", 0.0), 1)
                for outcome, n in (ms.get("outcomes") or {}).items():
                    tm["outcomes"][outcome] = tm["outcomes"].get(outcome, 0) + n
                if ms.get("n_503"):
                    tm["n_503"] = tm.get("n_503", 0) + ms["n_503"]
                _add_tokens(tm, ms)
    return out


def model_calls(accounting: dict) -> dict:
    """Contract 9: every kind of call's tries per model -- answered, tries,
    429s, 503s: what each free bucket was asked for."""
    out: dict = {}
    for stage in accounting.values():
        for model, ms in (stage.get("by_model") or {}).items():
            t = out.setdefault(model, {"answered": 0, "attempts": 0, "n_429": 0, "n_503": 0})
            t["answered"] += ms.get("answered", 0)
            t["attempts"] += ms.get("attempts", 0)
            t["n_429"] += (ms.get("outcomes") or {}).get("429", 0)
            t["n_503"] += ms.get("n_503", 0)
    return out


class Accounting:
    """Every Gemini call and model try of the current case run. Sorting's
    batches run on pool threads at once, so adding takes a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[dict] = []
        self.attempts: list[dict] = []

    def clear(self) -> None:
        with self._lock:
            self.calls, self.attempts = [], []

    def add_call(self, call: dict) -> None:
        with self._lock:
            self.calls.append(call)

    def add_attempt(self, attempt: dict) -> None:
        with self._lock:
            self.attempts.append(attempt)

    def summary(self) -> dict:
        with self._lock:
            return accounting_summary(list(self.calls), list(self.attempts))

    def call_list(self) -> list[dict]:
        """Every call, in the order they ended: purpose, the model that
        answered, seconds, tries, tokens, and the images it sent by
        resolution."""
        with self._lock:
            return [dict(c) for c in self.calls]

    def call_counts(self) -> Counter:
        with self._lock:
            return Counter(c["purpose"] for c in self.calls)

    def answered_by(self) -> Counter:
        """"purpose: model" per answer, as the earlier versions logged it:
        a quiet fallback to a weaker model changes the score."""
        with self._lock:
            return Counter(f"{a['purpose']}: {a['model']}" for a in self.attempts if a["outcome"] == "ok")


ACCOUNTING = Accounting()
# The purpose of the call_structured call running on this thread, and its
# last try (model, outcome): runs.note_attempt's record carries neither.
_tls = threading.local()


def _images(input_parts) -> dict:
    """The images a call sends, by the resolution each carries ("default"
    without one): shows whether --image-resolution reached the request."""
    out: dict[str, int] = {}
    for part in input_parts or []:
        if isinstance(part, dict) and part.get("type") == "image":
            name = str(part.get("resolution") or "default")
            out[name] = out.get(name, 0) + 1
    return out


def _close_ups(input_parts) -> int:
    """The close-ups a call sends (--read-crops): one label part each."""
    return sum(1 for part in input_parts or [] if isinstance(part, dict) and part.get("type") == "text"
               and str(part.get("text") or "").startswith(CLOSE_UP_LABEL))


def _counting(fn):
    """Wraps call_structured: each call's purpose and wall-clock time, and
    (through _note, on the same thread) its tries and tokens."""
    def wrapper(*args, **kwargs):
        purpose = str(kwargs.get("purpose") or "unnamed")
        outer = (getattr(_tls, "purpose", None), getattr(_tls, "last", None), getattr(_tls, "call", None))
        call = {"purpose": purpose, "seconds": 0.0, "ok": False, "model": None, "attempts": 0}
        images = _images(kwargs.get("input_parts"))
        if images:
            call["images"] = images
        crops = _close_ups(kwargs.get("input_parts"))
        if crops:
            call["crops"] = crops
        _tls.purpose, _tls.last, _tls.call = purpose, None, call
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            call["ok"] = True
            return result
        finally:
            call["seconds"] = round(time.monotonic() - started, 1)
            ACCOUNTING.add_call(call)
            _tls.purpose, _tls.last, _tls.call = outer
    wrapper._bench_wrapped = True
    return wrapper


def _note(attempt: dict) -> None:
    """One model try as call_structured reports it (runs.note_attempt).
    Only model, outcome, time and waits are kept -- never the key's last
    four characters. waited_s is the rate-limit slot wait, except on the
    retry after a 429, where it is the delay Google asked for."""
    try:
        model = str(attempt.get("model"))
        outcome = str(attempt.get("outcome"))
        waited = attempt.get("waited_s")
        waited = float(waited) if isinstance(waited, (int, float)) and not isinstance(waited, bool) else 0.0
        after_429 = getattr(_tls, "last", None) == (model, "429")
        kept = {
            "purpose": getattr(_tls, "purpose", None) or "unnamed",
            "model": model,
            "outcome": outcome,
            "seconds": round(float(attempt.get("ms") or 0) / 1000, 1),
            "slot_wait_s": 0.0 if after_429 else waited,
            "wait_429_s": waited if after_429 else 0.0,
        }
        if attempt.get("busy"):
            kept["busy"] = True
        _add_tokens(kept, attempt)
        ACCOUNTING.add_attempt(kept)
        call = getattr(_tls, "call", None)
        if call is not None:
            call["attempts"] += 1
            if outcome == "ok":
                call["model"] = model
            _add_tokens(call, attempt)
        _tls.last = (model, outcome)
    except Exception:  # accounting must never fail a Gemini call
        log.warning("benchmark: couldn't account a Gemini try", exc_info=True)


def _noting(fn):
    def wrapper(attempt):
        try:
            fn(attempt)
        finally:
            _note(attempt)
    wrapper._bench_wrapped = True
    return wrapper


# ---------------------------------------------------------------- the app

_APP: SimpleNamespace | None = None


def _app() -> SimpleNamespace:
    """The app's modules, imported once. call_structured is wrapped at its
    source before triage, extraction and pipeline import it by name, so
    every Gemini call is counted under its purpose -- including steps added
    later in other modules."""
    global _APP
    if _APP is not None:
        return _APP
    for path in (str(BOT_DIR), str(SCRIPTS_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import app.ai.runs as runs
    import app.ai.structured as structured

    if not getattr(structured.call_structured, "_bench_wrapped", False):
        structured.call_structured = _counting(structured.call_structured)
    if not getattr(runs.note_attempt, "_bench_wrapped", False):
        runs.note_attempt = _noting(runs.note_attempt)

    import app.ai.extraction as extraction
    import app.ai.second_look as second_look
    import app.ai.triage as triage

    import app.ai.zone_read as zone_read

    # Contract 9: the re-check's lite call, and its Decisions API requests
    # (not call_structured: ai/decisions.decide, counted the same way).
    import app.ai.check as check
    import app.ai.decisions as decisions

    # Contract 10: the linked check (a stub without a call until it is built).
    import app.ai.link_check as link_check

    for module in (triage, extraction, second_look, zone_read, check, link_check):  # imported before the wrap (not today)
        if hasattr(module, "call_structured") and not getattr(module.call_structured, "_bench_wrapped", False):
            module.call_structured = structured.call_structured
    if not getattr(decisions.decide, "_bench_wrapped", False):
        decisions.decide = _counting(decisions.decide)

    from app.ai.keys import AiActor
    from app.config import settings
    from app.intake import pipeline
    from app.intake.photos import intake_dir, ordered_photos
    from app.parts.history import family_and_side, family_of, import_case, normalise_item, parse_report, side_of
    from app.parts.prefill import prefill_annex_a
    from app.reports.db import SessionLocal, init_db
    from app.reports.models import IntakePhoto, IntakeSession, PastAnnexLine, PastLabourLine, Report, gen_id
    from app.reports.schema import SecurityIncidentDraft
    from app.reports.taxonomy import CANONICAL_PARTS
    from app.storage.files import generate_preview

    _APP = SimpleNamespace(
        runs=runs, structured=structured, extraction=extraction, triage=triage, AiActor=AiActor,
        settings=settings, pipeline=pipeline, intake_dir=intake_dir, ordered_photos=ordered_photos,
        import_case=import_case, normalise_item=normalise_item, parse_report=parse_report, side_of=side_of,
        family_of=family_of, family_and_side=family_and_side,
        prefill_annex_a=prefill_annex_a, SessionLocal=SessionLocal, init_db=init_db, IntakePhoto=IntakePhoto,
        IntakeSession=IntakeSession, PastAnnexLine=PastAnnexLine, PastLabourLine=PastLabourLine, Report=Report,
        gen_id=gen_id, SecurityIncidentDraft=SecurityIncidentDraft, CANONICAL_PARTS=CANONICAL_PARTS,
        generate_preview=generate_preview,
    )
    return _APP


def load_case(folder: Path) -> str:
    """A fresh intake draft holding every photo in the folder, in file-name
    (camera) order -- what dropping the folder on the wizard produces."""
    A = _app()
    files = sorted((p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name.lower())
    db = A.SessionLocal()
    try:
        report = A.Report(
            channel="manual", reporter_chat_id="benchmark", status="intake",
            data=A.SecurityIncidentDraft(description="", category=["Vehicle Collision or Damage"]).model_dump(),
        )
        db.add(report)
        db.flush()
        db.add(A.IntakeSession(report_id=report.id))
        for position, src in enumerate(files):
            photo_id = A.gen_id()
            dest = A.intake_dir(report.id) / f"{photo_id}{src.suffix.lower()}"
            shutil.copyfile(src, dest)
            A.generate_preview(str(dest))
            db.add(A.IntakePhoto(
                id=photo_id, report_id=report.id, position=position, original_name=src.name, path=str(dest),
                sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), size_bytes=dest.stat().st_size,
            ))
        db.commit()
        return report.id
    finally:
        db.close()


# ---------------------------------------------------------------- sort cache

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def sort_cache_dir(root: Path) -> tuple[Path, dict]:
    """The cache folder for the sorting step as it is now: the triage model
    chain, triage.SORT_PROMPT_VERSION ("v0" when there is none) and a
    fingerprint of the sorting prompt and schema. The fingerprint is there
    because a branch that changes the prompt or the area list without
    bumping a version must not be handed sorts made by the old prompt."""
    A = _app()
    chain = ",".join(m.strip() for m in A.settings.gemini_triage_model_chain.split(",") if m.strip())
    # The sort as it is asked now (contract 8 D1: with or without the damage
    # box); a branch from before those functions has only the constants.
    version_of, prompt_of, schema_of = (getattr(A.triage, name, None)
                                        for name in ("sort_prompt_version", "system_prompt", "response_schema"))
    version = str(version_of() if callable(version_of) else getattr(A.triage, "SORT_PROMPT_VERSION", "v0"))
    prompt = json.dumps([prompt_of() if callable(prompt_of) else getattr(A.triage, "SYSTEM_PROMPT", ""),
                         schema_of() if callable(schema_of) else getattr(A.triage, "_RESPONSE_SCHEMA", None)],
                        sort_keys=True, default=str)
    fingerprint = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    # A sorting thinking level sorts differently: a folder of its own. Unset,
    # the tag is what it was before the setting existed, so the caches
    # already filled stay in use.
    level = A.settings.gemini_thinking_level_sort
    setup = f"{chain}|{version}|{fingerprint}" + (f"|thinking={level}" if level else "")
    tag = hashlib.sha256(setup.encode("utf-8")).hexdigest()[:16]
    meta = {"dir": str(root / tag), "triage_model_chain": chain, "sort_prompt_version": version, "prompt_fingerprint": fingerprint}
    if level:
        meta["thinking_level_sort"] = level
    folder = root / tag
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "meta.json").exists():
        _write_json(folder / "meta.json", meta)
    return folder, meta


def _sort_fields() -> tuple:
    """SORT_FIELDS, and the damage box while the sort makes one (contract 8
    D1), whose sorts have a cache folder of their own (sort_cache_dir)."""
    box_on = getattr(_app().triage, "damage_box_on", None)
    return SORT_FIELDS + ("damage_box",) if callable(box_on) and box_on() else SORT_FIELDS


def apply_sort_cache(report_id: str, cache: Path) -> int:
    """Gives every unsorted photo with a cached result that result, as
    sorting would have written it. Returns how many came from the cache;
    pipeline._sort then sends only the rest (none: no call at all)."""
    A = _app()
    fields = _sort_fields()
    db = A.SessionLocal()
    hits = 0
    try:
        for photo in A.ordered_photos(db, report_id):
            if photo.kind is not None or not _SHA256.fullmatch(photo.sha256 or ""):
                continue
            path = cache / f"{photo.sha256}.json"
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(entry, dict) or not entry.get("kind"):
                continue
            for field in fields:
                setattr(photo, field, entry.get(field))
            photo.shows_damage = entry.get("shows_damage") is True
            hits += 1
        db.commit()
    finally:
        db.close()
    return hits


def store_sort_cache(report_id: str, cache: Path) -> int:
    """Keeps every sorted photo's result that isn't cached yet. Returns how
    many were written."""
    A = _app()
    fields = _sort_fields()
    db = A.SessionLocal()
    written = 0
    try:
        for photo in A.ordered_photos(db, report_id):
            if photo.kind is None or not _SHA256.fullmatch(photo.sha256 or ""):
                continue
            path = cache / f"{photo.sha256}.json"
            if path.exists():
                continue
            _write_json(path, {field: getattr(photo, field) for field in fields})
            written += 1
    finally:
        db.close()
    return written


# ---------------------------------------------------------------- past reports


class ReportSet:
    """The firm's past reports (--reports): each case's own report is held
    out and scored against; the others are the history prefill learns from."""

    def __init__(self, folder: Path):
        # Only the report reader: --rescore reads reports without loading the
        # app (and its database).
        from app.parts.history import parse_report

        self.items: list[tuple[Path, object]] = []
        self.skipped = 0
        paths = sorted(p for p in Path(folder).rglob("*")
                       if p.is_file() and p.suffix.lower() in REPORT_EXTENSIONS and not p.name.startswith("~$"))
        for path in paths:
            try:
                self.items.append((path, parse_report(path)))
            except Exception:  # not a report (ValueError) or an unreadable sheet
                self.skipped += 1
        # Every word of every Condition cell: the firm's own vocabulary.
        self.firm_words = {w for _, report in self.items for line in report.lines for w in words(line.condition)}

    def for_case(self, case: dict):
        """(path, parsed report) for a key case: by plate, else by a folder
        on the report's path named like the case's photo folder."""
        plate = _alnum((case.get("vehicle") or {}).get("plate_number"))
        if plate:
            for item in self.items:
                if _alnum(item[1].plate) == plate:
                    return item
        folder = str(case.get("folder") or "").casefold()
        if folder:
            for item in self.items:
                if any(part.casefold() == folder for part in item[0].parts[:-1]):
                    return item
        return None

    def others(self, held) -> list:
        return [item for item in self.items if item[0] != held[0]]


def load_history(items: list) -> None:
    """The scratch database's past-report history becomes exactly these
    reports."""
    A = _app()
    db = A.SessionLocal()
    try:
        db.query(A.PastAnnexLine).delete()
        db.query(A.PastLabourLine).delete()
        for path, report in items:
            A.import_case(db, report, path.name)
        db.commit()
    finally:
        db.close()


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _within(ours: float, theirs: float, share: float = 0.10) -> bool:
    # Half a cent of slack, so 110.00 against 100.00 is within 10% in floats.
    return abs(ours - theirs) <= share * abs(theirs) + 0.005


def score_prices(lines, rows: list[dict], pairs: dict[str, str], normalise) -> dict:
    """An Annex A's prices against the held-out report's: the W/shop Est
    and adjusted totals of the whole sheet (matched or not, so a missed or
    an extra line shows in the difference), and for each matched line
    (pairs: report wording -> row wording, both normalised, as
    eval_parts_kits.matched_pairs pairs them) whether the row has a price
    and whether it is within 10% of the surveyor's W/shop Est. That column,
    not the adjusted cost, is what prefill writes (the workshop's usual
    ask); the adjusted cost is the surveyor's decision on it. Lines or rows
    that share a wording are added up: lamps written LH and RH are one."""
    theirs: dict[str, float] = {}
    for line in lines:
        key = normalise(line.item)
        theirs[key] = theirs.get(key, 0.0) + _money(getattr(line, "workshop_est", 0))
    named = [r for r in rows if r.get("part_name")]
    ours: dict[str, float] = {}
    for row in named:
        key = normalise(row["part_name"])
        ours[key] = ours.get(key, 0.0) + _money(row.get("workshop_est"))
    total = sum(_money(r.get("workshop_est")) for r in named)
    report_total = sum(_money(getattr(line, "workshop_est", 0)) for line in lines)
    adjusted = sum(_money(r.get("adjusted_cost")) for r in named)
    report_adjusted = sum(_money(getattr(line, "adjusted_cost", 0)) for line in lines)
    return {
        "price_total": round(total, 2),
        "report_price_total": round(report_total, 2),
        "price_diff": round(total - report_total, 2),
        "adjusted_total": round(adjusted, 2),
        "report_adjusted_total": round(report_adjusted, 2),
        "adjusted_diff": round(adjusted - report_adjusted, 2),
        "matched_priced": sum(1 for s in pairs.values() if ours.get(s, 0.0) > 0),
        "within_10pct": sum(1 for a, s in pairs.items() if _within(ours.get(s, 0.0), theirs.get(a, 0.0))),
        "matched_abs_diff": round(sum(abs(ours.get(s, 0.0) - theirs.get(a, 0.0)) for a, s in pairs.items()), 2),
    }


def score_annex_a(held, drafted_items: list[dict]) -> dict:
    """Annex A as prefill builds it from the drafted damage list (make and
    model from the held-out report, history = the other reports, loaded by
    load_history) against the held-out report's real lines, compared with
    eval_parts_kits._matched as the 2026-11-05 numbers were: lines covered,
    rows written, priced and unpriced; extra = distinct row wordings that
    match no real line; canonical rows = rows named exactly like a
    taxonomy part, which the firm never writes (target 0). The same with
    the report's own damaged parts as the damage list gives the ceiling
    that history allows (perfect detection). The prices against the
    surveyor's: score_prices, and its lines within 10% at that ceiling."""
    A = _app()
    from eval_parts_kits import damaged_parts, matched_pairs

    real = {A.normalise_item(line.item) for line in held.lines}
    canonical = set(A.CANONICAL_PARTS)
    db = A.SessionLocal()
    try:
        def prefill(items: list[dict]) -> list[dict]:
            data = {
                "vehicle_info": {"make": held.make, "model": held.model},
                "damage_summary": copy.deepcopy(items),
                "damaged_parts": [i["part"] for i in items],
                "annex_a": {}, "annex_b": {},
            }
            return A.prefill_annex_a(db, data)["annex_a"]["items"]

        rows = prefill([i for i in drafted_items if i.get("part")])
        perfect = prefill([{"part": p, "damage_type": ""} for p in damaged_parts(held)])
    finally:
        db.rollback()
        db.close()
    keys = {A.normalise_item(r["part_name"]) for r in rows if r.get("part_name")}
    pairs = matched_pairs(real, keys)
    hit = set(pairs)
    perfect_pairs = matched_pairs(real, {A.normalise_item(r["part_name"]) for r in perfect if r.get("part_name")})
    perfect_hit = set(perfect_pairs)
    perfect_prices = score_prices(held.lines, perfect, perfect_pairs, A.normalise_item)
    priced = sum(1 for r in rows if float(r.get("workshop_est") or 0) > 0)
    named = [r["part_name"] for r in rows if r.get("part_name") in canonical]
    return {
        "real_lines": len(real),
        "rows": len(rows),
        "priced": priced,
        "unpriced": len(rows) - priced,
        "covered": len(hit),
        "coverage": _ratio(len(hit), len(real)),
        # _matched pairs each covered line with a different wording.
        "extra": len(keys) - len(hit),
        "canonical_rows": len(named),
        "canonical_names": sorted(set(named)),
        "perfect_rows": len(perfect),
        "perfect_covered": len(perfect_hit),
        "perfect_coverage": _ratio(len(perfect_hit), len(real)),
        **score_prices(held.lines, rows, pairs, A.normalise_item),
        "perfect_price_diff": perfect_prices["price_diff"],
        "perfect_within_10pct": perfect_prices["within_10pct"],
    }


# ---------------------------------------------------------------- one run

_ITEM_FIELDS = ("part", "other_part_name", "damage_type", "condition", "condition_tokens", "remarks", "severity",
                "ai_confidence", "side_inferred")
# Contract 8 D2: which reads found the part, in which photos, which cells.
_ITEM_FIELDS += ("found_by", "seen_in", "grid_cells")
# Contract 9: its photo and box, the AI's reason and its evidence (photos by
# case label: relabel_refs).
_ITEM_FIELDS += ("photo_reference", "bbox_2d", "reason", "evidence")
# A suggestion as a run keeps it (second_look.items; contract 9).
_SUGGESTION_FIELDS = ("key", "part", "name", "other_part_name", "source", "confidence", "found_by", "photo_reference",
                      "seen_in", "reason", "evidence", "check")
# Contract 10, only while the linked parts are on (so a run without them is
# kept as before): a suggestion's own words on how hard the part was hit,
# and its link -- the listed parts it hangs off, the kind, the table's rule,
# the parent's hit, the bucket, hidden / strip-down, the side it took and
# from where, the zone reads that called it intact, the linked check's
# answer. A zone suggestion joined to a link carries the link fields too.
_LINK_SUGGESTION_FIELDS = ("severity", "condition_tokens", "remarks", "linked_from", "link_type", "link_rule", "hit",
                           "bucket", "hidden", "needs_strip_down", "side_from", "intact_in", "link_check")
# The assembly walk, only while it acts: a second hop's hop and middle part,
# and the yes signs that lifted a linked part or opened its chain.
_WALK_SUGGESTION_FIELDS = ("hop", "via", "signs_yes")


def relabel_refs(entry: dict, label_of: dict[str, str]) -> dict:
    """A copy with its photos ("@<photo id>" in a draft) by case label, as
    the run's sorted_photos number them: photo_reference, seen_in, each
    evidence entry's photo. A photo gone since keeps its "@id"."""
    out = dict(entry)
    if isinstance(out.get("photo_reference"), str):
        out["photo_reference"] = label_of.get(out["photo_reference"], out["photo_reference"])
    if isinstance(out.get("seen_in"), list):
        out["seen_in"] = [label_of.get(r, r) for r in out["seen_in"]]
    if isinstance(out.get("evidence"), list):
        out["evidence"] = [dict(e, photo=label_of.get(e.get("photo"), e.get("photo"))) if isinstance(e, dict) else e
                           for e in out["evidence"]]
    return out


def suggestion_rows(look: dict, label_of: dict[str, str], fields: tuple = _SUGGESTION_FIELDS,
                    items: list | None = None) -> list[dict]:
    """The second look's items (zone finds and its own) as a run keeps them;
    or `items` (contract 10: the linked parts), with `fields`."""
    items = look.get("items") if items is None else items
    return [relabel_refs({k: it.get(k) for k in fields if k in it}, label_of)
            for it in items or [] if isinstance(it, dict) and it.get("part")]


def links_mode(settings) -> str:
    """settings.intake_links: "off" (and before contract 10), "shadow" or
    "on"."""
    mode = getattr(settings, "intake_links", "off")
    return mode if mode in ("shadow", "on") else "off"


def _link_table():
    """The link table the app loaded (None when it didn't)."""
    from app.intake import links

    return links.TABLE


# ---------------------------------------------------------------- --keep-reads (contract 10)


class KeptReads:
    """--keep-reads: what the overall reading answered (extraction.
    draft_report's damage items), the sides side_from_sorter changed, and
    the reads zones.merge_reads merged (the overall items and each zone
    read's, and what came out) -- kept by wrapping those functions on the
    pipeline's and zones' modules in this harness only: no app change, no
    extra call. For Z's P0 re-run (plans/2026-11-17-linked-parts/
    Z-report.md section 7)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.kept: dict = {}

    def clear(self) -> None:
        with self._lock:
            self.kept = {}

    def add(self, name: str, value) -> None:
        with self._lock:
            self.kept.setdefault(name, []).append(value)

    def taken(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.kept)


KEPT_READS = KeptReads()


def _kept_item(item) -> dict:
    return {k: copy.deepcopy(item.get(k)) for k in _ITEM_FIELDS if k in item} if isinstance(item, dict) else {}


def _keeping_draft(fn):
    """extraction.draft_report as the pipeline calls it: its damage items
    as the model gave them (photos as the labels sent, "P07")."""
    def wrapper(*args, **kwargs):
        draft = fn(*args, **kwargs)
        try:
            items = draft.model_dump().get("damage_summary") if hasattr(draft, "model_dump") else None
            KEPT_READS.add("overall", [_kept_item(i) for i in items or []])
        except Exception:  # keeping must never fail a reading
            log.warning("benchmark: couldn't keep the overall reading's answer", exc_info=True)
        return draft
    wrapper._bench_wrapped = True
    return wrapper


def _keeping_sides(fn):
    """pipeline.side_from_sorter: each part whose side it changed."""
    def wrapper(items, *args, **kwargs):
        before = [str(i.get("part")) if isinstance(i, dict) else None for i in items]
        result = fn(items, *args, **kwargs)
        try:
            KEPT_READS.add("side_from_sorter", [{"from": b, "to": i.get("part"), "photo_reference": i.get("photo_reference")}
                                                for b, i in zip(before, items) if isinstance(i, dict) and i.get("part") != b])
        except Exception:
            log.warning("benchmark: couldn't keep side_from_sorter's changes", exc_info=True)
        return result
    wrapper._bench_wrapped = True
    return wrapper


def _keeping_merge(fn):
    """zones.merge_reads: the overall items and each zone read's that went in,
    and the list and the disputed names that came out."""
    def wrapper(overall, reads, *args, **kwargs):
        taken = {"overall": [_kept_item(i) for i in overall or []],
                 "zones": [{"read": name, "items": [_kept_item(i) for i in found or []]} for name, found in reads or []]}
        merged, disputed = fn(overall, reads, *args, **kwargs)
        try:
            taken.update(merged=[_kept_item(i) for i in merged or []], disputed=sorted(disputed or []))
            KEPT_READS.add("merge", taken)
        except Exception:
            log.warning("benchmark: couldn't keep the zone merge", exc_info=True)
        return merged, disputed
    wrapper._bench_wrapped = True
    return wrapper


def keep_reads_on(A) -> None:
    """--keep-reads: the three wrappers in place (once)."""
    import app.intake.zones as zones

    if not getattr(A.pipeline.draft_report, "_bench_wrapped", False):
        A.pipeline.draft_report = _keeping_draft(A.pipeline.draft_report)
    if not getattr(A.pipeline.side_from_sorter, "_bench_wrapped", False):
        A.pipeline.side_from_sorter = _keeping_sides(A.pipeline.side_from_sorter)
    if not getattr(zones.merge_reads, "_bench_wrapped", False):
        zones.merge_reads = _keeping_merge(zones.merge_reads)


def kept_reads(label_of: dict[str, str]) -> dict:
    """This run's kept reads, photos by case label: one entry per call of
    each wrapped function (a reading calls each once)."""
    out = KEPT_READS.taken()
    for name in ("overall", "side_from_sorter"):
        if name in out:
            out[name] = [[relabel_refs(i, label_of) for i in items] for items in out[name]]
    for merge in out.get("merge") or []:
        merge["overall"] = [relabel_refs(i, label_of) for i in merge.get("overall") or []]
        merge["merged"] = [relabel_refs(i, label_of) for i in merge.get("merged") or []]
        for zone in merge.get("zones") or []:
            zone["items"] = [relabel_refs(i, label_of) for i in zone.get("items") or []]
    return out


def run_case(case: dict, cases_dir: Path, *, sort_cache: Path | None = None, reports: ReportSet | None = None,
             keep_photos: bool = False, keep_reads: bool = False) -> dict:
    A = _app()
    held = reports.for_case(case) if reports else None
    if held is not None:
        # Loaded before reading, not only before scoring: the firm's history
        # as it would be for this case (every other report, not its own), so
        # a reading step that consults past cases is leave-one-out as well.
        load_history(reports.others(held))
    rid = load_case(cases_dir / case["folder"])
    ACCOUNTING.clear()
    KEPT_READS.clear()
    out: dict = {"case": case["name"], "report_id": rid}
    cache_info = {"hits": apply_sort_cache(rid, sort_cache)} if sort_cache else None
    started = time.monotonic()
    try:
        # The company keys, as the benchmark always ran on.
        A.pipeline._sort(rid, A.AiActor.cli())
        out["sort_seconds"] = round(time.monotonic() - started, 1)
        started = time.monotonic()
        A.pipeline._read(rid, A.AiActor.cli())
        out["read_seconds"] = round(time.monotonic() - started, 1)
    except Exception as exc:  # recorded, and the next case still runs
        out["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if sort_cache:
            cache_info["stored"] = store_sort_cache(rid, sort_cache)
    out["calls"] = ACCOUNTING.call_counts()
    out["answered_by"] = ACCOUNTING.answered_by()
    out["accounting"] = ACCOUNTING.summary()
    out["ai_calls"] = ACCOUNTING.call_list()

    db = A.SessionLocal()
    try:
        report = db.get(A.Report, rid)
        session = db.get(A.IntakeSession, rid)
        photos = A.ordered_photos(db, rid)
        read_ids = set(session.read_photo_ids or [])
        data = report.data or {}
        out["photos"] = len(photos)
        out["read"] = len(read_ids)
        if cache_info is not None:
            cache_info["misses"] = len(photos) - cache_info["hits"]
            out["sort_cache"] = cache_info
        out["kinds"] = dict(Counter(p.kind or "unsorted" for p in photos))
        out["read_kinds"] = dict(Counter(p.kind or "unsorted" for p in photos if p.id in read_ids))
        damage_photos = [p for p in photos if p.shows_damage]
        out["damage_photos_read"] = f"{len([p for p in damage_photos if p.id in read_ids])}/{len(damage_photos)}"
        # The sorter's box too while it makes one (contract 8 D1).
        boxes = "damage_box" in _sort_fields()
        out["sorted_photos"] = [
            {"n": i + 1, "file": p.original_name, "kind": p.kind, "area": p.area, "quality": p.quality,
             "damage": p.shows_damage, "part": p.part, "read": p.id in read_ids, "note": p.note,
             **({"box": p.damage_box} if boxes else {})}
            for i, p in enumerate(photos)
        ]
        items = [item for item in data.get("damage_summary") or [] if isinstance(item, dict)]
        drafted = [item.get("part") for item in items]
        label_of = {f"@{p.id}": f"P{i + 1:02d}" for i, p in enumerate(photos)}
        out["drafted_parts"] = drafted
        out["drafted_items"] = [relabel_refs({k: item.get(k) for k in _ITEM_FIELDS if k in item}, label_of)
                                for item in items]
        out["parts"] = score_parts(case["parts"], drafted)
        # What a "parts to check" list would add: every part the sorter named
        # on a photo of the insured vehicle, damage visible or not -- surveyors
        # photograph close up the parts they go on to claim.
        hinted = sorted({p.part for p in photos if p.part} - set(drafted))
        out["checklist"] = hinted
        out["parts_with_checklist"] = score_parts(case["parts"], drafted + hinted)
        look = data.get("second_look") if isinstance(data.get("second_look"), dict) else None
        # Contract 10: with the linked parts on, they are scored apart
        # (score_links), so the other suggestions' numbers stay contract 9's:
        # "on" keeps them in second_look.items (source "link"), "shadow" in
        # the AI draft only.
        mode = links_mode(A.settings)
        fields = _SUGGESTION_FIELDS + (_LINK_SUGGESTION_FIELDS if mode != "off" else ())
        if mode != "off" and walk_on(A.settings):
            fields += _WALK_SUGGESTION_FIELDS
        link_items: list = []
        if look and mode != "off":
            look = dict(look)
            link_items = [it for it in look.get("items") or [] if isinstance(it, dict) and it.get("source") == "link"]
            look["items"] = [it for it in look.get("items") or [] if not (isinstance(it, dict) and it.get("source") == "link")]
        if mode == "shadow":
            link_items = [it for it in (session.ai_draft or {}).get("link_suggestions") or [] if isinstance(it, dict)]
        if look:
            suggested = [it.get("part") for it in look.get("items") or [] if isinstance(it, dict) and it.get("part")]
            out["second_look"] = {
                "status": look.get("status"), "model": look.get("model"), "note": look.get("note"),
                "seconds": look.get("seconds"), "photos_sent": look.get("photos_sent"),
                "candidates": look.get("candidates"), "suggested": suggested,
                "calls": out["calls"].get("second look", 0),
            }
            out["parts_with_second_look"] = score_second_look(case["parts"], out["parts"], drafted, suggested)
            # Contract 9: each suggestion in detail, real or wrong by the key,
            # and what the re-check did.
            out["suggestions"] = suggestion_rows(look, label_of, fields)
            mark_truth(out["suggestions"], case["parts"], out["parts"])
            out["second_look"]["check"] = look.get("check")
            if mode != "off":
                # The linked check's step (SecondLook.link_run; shadow: the
                # AI draft's).
                out["second_look"]["link_run"] = look.get("link_run") or (session.ai_draft or {}).get("link_run")
            # _read runs the second look and the re-check after the reading:
            # read_seconds stays the reading's own, comparable with runs from
            # before them.
            after = [look.get("seconds"), (look.get("check") or {}).get("seconds")]
            after = sum(x for x in after if isinstance(x, (int, float)) and not isinstance(x, bool))
            if after and "read_seconds" in out:
                out["read_seconds"] = round(max(0.0, out["read_seconds"] - after), 1)
        if mode != "off":
            # Contract 10: the linked parts as shown, scored as
            # scripts/bench/replay_links.py scores a stored run's.
            out["link_suggestions"] = suggestion_rows({}, label_of, fields, link_items)
            for row in out["link_suggestions"]:
                if isinstance(row.get("signs_yes"), list):
                    row["signs_yes"] = [dict(s, photo=label_of.get(s.get("photo"), s.get("photo")))
                                        if isinstance(s, dict) else s for s in row["signs_yes"]]
            others = [r.get("part") for r in out.get("suggestions") or []]
            out["links"] = score_links(case["parts"], drafted, others, out["link_suggestions"],
                                       inside=hidden_children(_link_table()))
            out["links"].update(mode=mode, zone_joined=sum(1 for r in out.get("suggestions") or [] if r.get("linked_from")),
                                impact=(data.get("vehicle_info") or {}).get("point_of_impact"))
        if keep_reads:
            out["reads"] = kept_reads(label_of)
        if walk_on(A.settings):
            # The assembly walk as the zone reads merged it, photos by label.
            out["assembly_walk"] = walk_rows((session.ai_draft or {}).get("assembly_walk"), label_of)
        out["vehicle"] = score_vehicle(case["vehicle"], data.get("vehicle_info") or {})
        out["sorter"] = sorter_quality(case["parts"], out["sorted_photos"])
        # Contract 8 D2: what each zone call sent and saw, from the run log's
        # zone row (runs.ZONES_KIND), photos by case label.
        AiRun = A.runs.AiRun
        zone_row = (db.query(AiRun).filter(AiRun.report_id == rid, AiRun.kind == A.runs.ZONES_KIND)
                    .order_by(AiRun.started_at.desc()).first())
        if zone_row is not None:
            detail = copy.deepcopy(zone_row.photos_sent) if isinstance(zone_row.photos_sent, dict) else {}
            for zone in detail.get("zones") or []:
                zone["photos"] = [label_of.get(ref, ref) for ref in zone.get("photos") or []]
            out["zones"] = {"status": zone_row.status, "error_code": zone_row.error_code, **detail}
    finally:
        db.close()

    contradicted = None
    if held is not None:
        out["conditions"] = score_conditions(case["parts"], out["parts"]["matched"], items, held[1].lines,
                                             reports.firm_words, A.normalise_item, A.side_of)
        out["annex_a"] = score_annex_a(held[1], items)
        contradicted = contradicted_parts(drafted, held[1].lines, A.family_of, A.side_of, A.family_and_side)
    out["parts_fair"] = score_fair(case["parts"], out["drafted_items"], out["parts"], contradicted)
    score_details(out, case, held[1].lines if held is not None else None, A.normalise_item, A.side_of)
    if not keep_photos:
        # A repeat run copies every case's photos again; on the live
        # server's small disk, a full disk stops the app taking uploads.
        shutil.rmtree(Path(A.settings.storage_dir) / "intake" / rid, ignore_errors=True)
    return out


# ---------------------------------------------------------------- printing


def summary_line(r: dict) -> str:
    if "parts" not in r:
        return f"{r['case']:<11} ERROR {r.get('error')}"
    p, v = r["parts"], r["vehicle"]
    facts = sum(1 for x in v.values() if x["verdict"] == "ok")
    return (f"{r['case']:<11} parts {p['found']:>2}/{p['expected']:<2} (visible {p['visible_found']:>2}/{p['visible_expected']:<2}) "
            f"wrong-side {len(p['wrong_side'])} extra {len(p['extra'])} | facts {facts}/{len(v)} | "
            f"read {r['read']}/{r['photos']} (damage {r['damage_photos_read']}) | "
            f"sort {r.get('sort_seconds', '-')}s read {r.get('read_seconds', '-')}s | models {dict(r['answered_by'])}"
            + (f" | ERROR {r['error']}" if r.get("error") else ""))


def _pct(value) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def detail_lines(r: dict) -> list[str]:
    lines = []
    if "parts" not in r:
        return lines
    lines.append(f"   missed (visible): {r['parts']['missed_visible']}")
    lines.append(f"   wrong side: {r['parts']['wrong_side']}  extra: {r['parts']['extra']}  "
                 f"side undetermined: {len(r['parts']['side_undetermined'])}")
    lines.append(f"   facts: { {k: (x['verdict'], x['got']) for k, x in r['vehicle'].items()} }")
    f = r.get("parts_fair")
    if f:
        lines.append(fair_line(f))
    lines += part_detail_lines(r)
    pc = r["parts_with_checklist"]
    lines.append(f"   + checklist {r['checklist']}")
    lines.append(f"   with checklist: parts {pc['found']}/{pc['expected']} (visible {pc['visible_found']}/{pc['visible_expected']}) "
                 f"wrong-side {len(pc['wrong_side'])} extra {len(pc['extra'])} {pc['extra']}")
    look, ls = r.get("second_look"), r.get("parts_with_second_look")
    if look and ls:
        lines.append(f"   second look: {look['status']} on {look.get('model') or '-'} in {look.get('seconds')}s, "
                     f"{look['calls']} call(s), {look.get('photos_sent')} photos, {look.get('candidates')} candidates; "
                     f"suggested {look['suggested']}" + (f" ({look['note']})" if look.get("note") else ""))
        lines.append(f"   if every suggestion were ticked: parts {ls['found']}/{r['parts']['expected']} "
                     f"(visible {ls['visible_found']}/{r['parts']['visible_expected']}), added {ls['added']} "
                     f"{ls['added_parts']}, extra {ls['extra']} {ls['extra_parts']}, wrong side {ls['wrong_side']}")
    lines += evidence_lines(r)
    lines += link_lines(r)
    s = r["sorter"]
    cache = r.get("sort_cache")
    lines.append(f"   sorter: labels in key {s['tagged_in_key']}/{s['tagged_photos']} ({_pct(s['label_precision'])}), "
                 f"key parts photographed {s['key_parts_photographed']}/{s['key_parts']}, "
                 f"key-part photos read {s['key_photos_read']}/{s['key_photos']} ({_pct(s['selection_coverage'])})"
                 + (f" | sort cache: {cache['hits']} from cache, {cache['stored']} stored" if cache else ""))
    stages = []
    for purpose, st in r["accounting"].items():
        models = ", ".join(f"{m} {ms['answered']}/{ms['attempts']}" for m, ms in st["by_model"].items())
        stages.append(f"{purpose}: {st['calls']} call(s) {st['seconds']}s, tries {st['attempts']} [{models}], "
                      f"slot wait {st['slot_wait_s']}s, 429 x{st['n_429']} waited {st['wait_429_s']}s{_tokens_text(st)}")
    lines.append("   AI: " + ("; ".join(stages) if stages else "no calls"))
    # Contract 8 D2: one line for the zone calls.
    zones = (r.get("zones") or {}).get("zones")
    if zones is not None:
        said = []
        for z in zones:
            text = f"{z.get('zone')} {z.get('status')} ({len(z.get('photos') or [])} photos"
            if z.get("status") == "ok":
                text += f", saw {z.get('side_seen')}, found {z.get('found')}, intact {len(z.get('intact') or [])}"
            if z.get("error_code"):
                text += f", {z['error_code']}"
            if z.get("note"):
                text += f"; {z['note']}"
            said.append(text + ")")
        lines.append("   zones: " + ("; ".join(said) if said else "none to read"))
    # Every call but sorting's batches (summed in the line above), one line
    # each: what a thinking level or a resolution cost.
    for call in r.get("ai_calls") or []:
        if call.get("purpose") == "photo sort":
            continue
        images = call.get("images")
        lines.append(f"   call {call['purpose']}: {call.get('model') or 'no answer'} in {call['seconds']}s, "
                     f"{call.get('attempts', 0)} tr{'y' if call.get('attempts') == 1 else 'ies'}{_tokens_text(call)}"
                     + (f", images {images}" if images else "") + (f", close-ups {call['crops']}" if call.get("crops") else ""))
    c = r.get("conditions")
    if c:
        lines.append(f"   conditions: {c['scored']} scored, exact {c['exact']}, first word {c['first_token']}, "
                     f"word recall {_pct(c['token_recall'])}; firm words only {c['firm_words_only']}/{c['drafted_conditions']}"
                     + (f", other words {c['words_not_in_firm_reports']}" if c["words_not_in_firm_reports"] else ""))
    a = r.get("annex_a")
    if a:
        lines.append(f"   Annex A (leave-one-out): covered {a['covered']}/{a['real_lines']} ({_pct(a['coverage'])}); "
                     f"rows {a['rows']} (priced {a['priced']}, unpriced {a['unpriced']}, extra {a['extra']}, "
                     f"canonical names {a['canonical_rows']}); perfect detection {a['perfect_covered']}/{a['real_lines']}")
        if "price_diff" in a:
            lines.append(f"   Annex A prices: W/shop Est {a['price_total']:.0f} against the surveyor's "
                         f"{a['report_price_total']:.0f} ({a['price_diff']:+.0f}), adjusted {a['adjusted_total']:.0f} "
                         f"against {a['report_adjusted_total']:.0f} ({a['adjusted_diff']:+.0f}); matched lines priced "
                         f"{a['matched_priced']}/{a['covered']}, within 10% {a['within_10pct']} "
                         f"(perfect detection {a['perfect_within_10pct']})")
    return lines


def evidence_lines(r: dict) -> list[str]:
    """Contract 9: the suggestions' and the list's boxes and reasons, and the
    re-check's verdicts on the real and the wrong suggestions."""
    m = evidence_metrics(r)
    lines = []
    if "sug_n" in m:
        said = f"   suggestions: {m['sug_n']}"
        if "sug_real" in m:
            said += f" (real {m['sug_real']}, wrong {m['sug_wrong']})"
        said += (f"; AI box {m['sug_box']}/{m['sug_n']} (any box {m['sug_any_box']}), "
                 f"reason {m['sug_reason']}/{m['sug_n']}")
        if "list_n" in m:
            said += f" | list: AI box {m['list_box']}/{m['list_n']}, reason {m['list_reason']}/{m['list_n']}"
        lines.append(said)
    check = (r.get("second_look") or {}).get("check")
    if isinstance(check, dict):
        said = (f"   re-check: {check.get('status')}" + (f" by {check['by']}" if check.get("by") else "")
                + (f" on {check['model']}" if check.get("model") else "") + f" in {check.get('seconds')}s, "
                f"{check.get('calls', 0)} call(s)" + (f" ({check['note']})" if check.get("note") else ""))
        if "chk_real_yes" in m:
            said += "; " + "; ".join(f"{group} " + " ".join(f"{v} {m[f'chk_{group}_{v}']}" for v in VERDICTS)
                                     for group in ("real", "wrong"))
        lines.append(said)
    return lines


def link_lines(r: dict) -> list[str]:
    """Contract 10: a run's linked parts against the key, the recall they
    add, and the linked check's step."""
    b = r.get("links")
    if not isinstance(b, dict) or "shown" not in b:
        return []
    lines = [f"   linked parts ({b.get('mode') or '-'}): {b['shown']} shown; real {b['real']} {b['real_parts']}, "
             f"wrong side {b['wrong_side']} {b['wrong_side_parts']}, extra {b['extra']} {b['extra_parts']}; "
             f"strip-down wrong {b['strip_down_wrong']}",
             f"   if all ticked: list {b['list_found']}, + suggestions {b['lz_found']}, + links {b['lzl_found']} of "
             f"{b['expected']}; inside parts {b['lz_inside_found']} -> {b['lzl_inside_found']} of {b['inside_expected']}"]
    run = (r.get("second_look") or {}).get("link_run")
    if isinstance(run, dict):
        lines.append(f"   linked check: {run.get('status')} on {run.get('model') or '-'} in {run.get('seconds')}s, "
                     f"{run.get('calls', 0)} call(s)" + (f" ({run['note']})" if run.get("note") else ""))
    return lines


def part_detail_lines(r: dict) -> list[str]:
    """The sides, zones and Replace / Repair of a run's found parts."""
    lines = []
    sd = r.get("part_sides")
    if sd:
        lines.append(f"   sides of the found parts: right {sd['right']} (inferred {sd['right_inferred']}), wrong "
                     f"{sd['wrong']} (inferred {sd['wrong_inferred']}), undetermined {sd['undetermined']}, "
                     f"of {sd['sided_found']} with a side")
    zn = r.get("part_zones")
    if zn:
        lines.append("   by zone, found/expected: " + "; ".join(
            f"{zone} {z['found']}/{z['expected']}" + (f" wrong side {z['wrong_side']}" if z["wrong_side"] else "")
            + (f" extra {z['extra']}" if z["extra"] else "")
            for zone, z in zn.items() if z["expected"] or z["extra"]))
    rr = r.get("part_remarks")
    if rr:
        off = [f"{d['part']} ({d['drafted']}, surveyor {d['surveyor']})" for d in rr["details"] if d["verdict"] == "disagree"]
        lines.append(f"   Replace/Repair: agree {rr['agree']}, disagree {rr['disagree']}, unsure {rr['unsure']} of "
                     f"{rr['scored']}; surveyor Repair {rr['surveyor_repair']}, drafted so {rr['repair_agree']}"
                     + (f"; disagree {off}" if off else ""))
    return lines


def _tokens_text(source: dict) -> str:
    said = [f"{label} {source[field]}" for field, label in (
        ("prompt_tokens", "in"), ("output_tokens", "out"), ("thought_tokens", "thinking"), ("tool_calls", "code runs"),
        ("tool_tokens", "tool"))
        if source.get(field) is not None]
    return f", tokens {' '.join(said)}" if said else ""


def fair_line(f: dict) -> str:
    contradicted = f.get("contradicted")
    return (f"   fair: parts {f['fair_found']:g}/{f['expected']} (visible {f['fair_visible_found']:g}/{f['visible_expected']}), "
            f"extra {f['fair_extra']} (strict {f['strict_extra']}); neighbours {f['neighbour']}; "
            f"Other named like a key part {f['other_named']}"
            + ("" if contradicted is None else f"; contradicted (surveyor: Serviceable) {contradicted}"))


def _num(value, digits: str = "g") -> str:
    # Tokens and dollars in whole numbers: "1.052e+04" and "35730.1" say less.
    return f"{value:.0f}" if abs(value) >= 1000 else f"{value:{digits}}"


def _fmt(st: dict | None) -> str:
    if not st:
        return "-"
    if st["n"] == 1 or st["min"] == st["max"]:
        return _num(st["mean"])
    # A price difference is below 0: "[-35730 to -34070]", not "[-35730--34070]".
    sep = " to " if st["min"] < 0 else "-"
    return f"{_num(st['mean'], '.4g')} [{_num(st['min'])}{sep}{_num(st['max'])}]"


TABLES = (
    (("found", "found"), ("visible", "visible_found"), ("extra", "extra"), ("wrong side", "wrong_side"),
     ("undetermined", "side_undetermined"), ("facts", "facts_ok"), ("read", "photos_read"),
     ("sort s", "sort_s"), ("read s", "read_s")),
    (("labels in key", "label_precision"), ("photographed", "photographed_recall"),
     ("selection", "selection_coverage"), ("calls", "calls"), ("429 wait s", "wait_429_s"),
     ("cond exact", "cond_exact_rate"), ("cond words", "cond_token_recall"), ("firm words", "cond_firm_share"),
     ("Annex A", "annex_coverage"), ("A extra", "annex_extra"), ("A canonical", "annex_canonical")),
    (("+look found", "look_found"), ("+look visible", "look_visible_found"), ("look added", "look_added"),
     ("look extra", "look_extra"), ("look wrong side", "look_wrong_side"), ("suggested", "look_suggested"),
     ("look calls", "look_calls"), ("look s", "look_s")),
    (("found", "found"), ("fair found", "fair_found"), ("fair visible", "fair_visible_found"), ("extra", "extra"),
     ("fair extra", "fair_extra"), ("neighbour", "neighbour"), ("Other named", "other_named"),
     ("contradicted", "contradicted")),
    (("read tok in", "read_tok_in"), ("read tok out", "read_tok_out"), ("read thinking", "read_tok_think"),
     ("read code runs", "read_code_runs"), ("read tool tok", "read_tok_tool"), ("read crops", "read_crops"),
     ("sort tok in", "sort_tok_in"), ("sort tok out", "sort_tok_out"),
     ("sort thinking", "sort_tok_think"), ("look tok in", "look_tok_in"), ("look thinking", "look_tok_think"),
     ("all tok in", "tok_in"), ("total s", "total_s")),
    # The found parts in detail (score_details).
    (("with a side", "sided_found"), ("side right", "side_right"), ("wrong side", "wrong_side"),
     ("side undetermined", "side_undetermined_found"), ("right inferred", "side_right_inferred"),
     ("wrong inferred", "side_wrong_inferred"), ("R/R scored", "remarks_scored"), ("R/R agree", "remarks_agree"),
     ("R/R disagree", "remarks_disagree"), ("R/R unsure", "remarks_unsure"), ("surveyor Repair", "remarks_repair"),
     ("Repair agree", "remarks_repair_agree")),
    tuple(pair for zone in ZONES for pair in ((f"{zone} found", f"zone_{zone}_found"), (f"{zone} of", f"zone_{zone}_expected"))),
    # The whole chain: the Annex A built from the drafted parts, its prices.
    (("A lines", "annex_coverage"), ("A covered", "annex_covered"), ("A of", "annex_lines"), ("A extra", "annex_extra"),
     ("A rows", "annex_rows"), ("A priced", "annex_priced"), ("price diff", "annex_price_diff"),
     ("price diff share", "annex_price_diff_rate"), ("adjusted diff", "annex_adjusted_diff"), ("matched priced", "annex_matched_priced"),
     ("within 10%", "annex_within_10"), ("matched |diff|", "annex_price_abs_diff"),
     ("perfect within 10%", "annex_perfect_within_10")),
)
# Contract 8 D2: the zone stage.
TABLES += (
    (("zone calls", "zone_calls"), ("zone failed", "zone_failed"), ("zone s", "zone_s"),
     ("zone tok in", "zone_tok_in"), ("zone tok out", "zone_tok_out"), ("zone thinking", "zone_tok_think"),
     ("zone only", "zone_only_parts"), ("both reads", "both_parts"), ("side flips", "zone_side_flips"),
     ("other end", "zone_other_end")),
)


# Contract 9: the suggestions' evidence, and the re-check.
TABLES += (
    (("sugg", "sug_n"), ("real", "sug_real"), ("wrong", "sug_wrong"), ("AI box", "sug_box"), ("box fill", "sug_box_fill"),
     ("sorter box", "sug_sort_box"), ("any box fill", "sug_any_box_fill"), ("reason", "sug_reason"), ("reason fill", "sug_reason_fill"), ("list box fill", "list_box_fill"),
     ("list reason fill", "list_reason_fill")),
    (("checked", "sug_checked"), ("real yes", "chk_real_yes"), ("real unsure", "chk_real_unsure"),
     ("real no", "chk_real_no"), ("wrong yes", "chk_wrong_yes"), ("wrong unsure", "chk_wrong_unsure"),
     ("wrong no", "chk_wrong_no"), ("real kept", "chk_real_kept_rate"), ("no on wrong", "chk_wrong_no_rate"),
     ("dec calls", "dec_calls"), ("dec s", "dec_s"), ("lite check calls", "check_calls"),
     ("re-check s", "recheck_s"), ("429", "n_429"), ("503", "n_503")),
)
# Contract 10: the linked parts (scored apart from the other suggestions),
# the recall they add, and the linked check's calls.
TABLES += (
    (("links", "link_shown"), ("real", "link_real"), ("wrong side", "link_wrong_side"), ("extra", "link_extra"),
     ("wrong/real", "link_wrong_per_real"), ("strip-down wrong", "link_strip_wrong"), ("hidden", "link_hidden_shown"),
     ("list+sugg", "lz_found"), ("+links", "lzl_found"), ("inside l+s", "lz_inside_found"),
     ("inside +links", "lzl_inside_found"), ("inside of", "link_inside_expected"),
     ("link check calls", "lchk_calls"), ("link check s", "lchk_s")),
)


def model_lines(by_model: dict) -> list[str]:
    """Contract 9: the calls per model, every kind of call together."""
    if not by_model:
        return []
    return ["", "calls per model (answered/tries, 429s, 503s): " + "; ".join(
        f"{model} {t['answered']}/{t['attempts']}" + (f" 429 x{t['n_429']}" if t["n_429"] else "")
        + (f" 503 x{t['n_503']}" if t["n_503"] else "") for model, t in by_model.items())]


def summary_table(summary: dict) -> list[str]:
    out = [f"SUMMARY: {summary['reps']} run(s) of {summary['cases_n']} case(s), mean [min-max]; "
           f"TOTAL over the {len(summary['complete_reps'])} complete run(s)"]
    rows = [(name, c["metrics"]) for name, c in summary["cases"].items()] + [("TOTAL", summary["total"])]
    for table in TABLES:
        present = [(head, key) for head, key in table if any(m.get(key) for _, m in rows)]
        if not present:
            continue
        cells = [["case"] + [head for head, _ in present]]
        cells += [[name] + [_fmt(m.get(key)) for _, key in present] for name, m in rows]
        widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
        out.append("")
        out += ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in cells]
    out += model_lines(summary.get("by_model") or {})
    if summary["errors"]:
        out.append(f"\nERRORS ({len(summary['errors'])}, left out of the numbers above):")
        out += [f"  {e['case']} run {e['rep']}: {e['error']}" for e in summary["errors"]]
    return out


# ---------------------------------------------------------------- main


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _chain(text: str) -> str:
    models = [m.strip() for m in text.split(",") if m.strip()]
    if not models:
        raise argparse.ArgumentTypeError("needs at least one model name")
    return ",".join(models)


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else None


# flag -> the Settings field it sets, and config's normaliser for it. The
# values are checked in main, once --scratch has pointed the app at its
# folder: app.config is not imported while the arguments are read.
MODEL_SETTINGS = (
    ("thinking_level_read", "gemini_thinking_level_read", "normal_thinking_levels"),
    ("thinking_level_sort", "gemini_thinking_level_sort", "normal_thinking_levels"),
    ("thinking_level_second_look", "gemini_thinking_level_second_look", "normal_thinking_levels"),
    ("image_resolution", "gemini_image_resolution", "normal_image_resolutions"),
    ("read_tools", "gemini_read_tools", "normal_read_tools"),
)
# Contract 8 D2: zone reading and its grid marks (--read-mode, --grid-marks).
MODEL_SETTINGS += (
    ("read_mode", "intake_read_mode", "normal_read_mode"),
    ("grid_marks", "intake_grid_marks", "normal_grid_marks"),
)
# Contract 8 D1: flag -> the Settings field it turns on (model_settings).
# In the result file's ai_settings only while on, so a run without them
# reads exactly as before.
D1_SETTINGS = (
    ("read_tools_timeout", "gemini_read_tools_timeout"),
    ("zoom_hint", "gemini_read_zoom_hint"),
    ("sort_damage_box", "intake_sort_damage_box"),
    ("read_crops", "intake_read_crops"),
)


def _seconds(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return value


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="benchmark_intake.py",
        description="Scores the intake AI (sorting + reading, real Gemini calls) against the surveyors' reports. "
                    "Needs BENCHMARK_SCRATCH=1 with DATABASE_URL and STORAGE_DIR pointing at scratch locations.",
        epilog="On the server: scripts/bench/run_bench.sh LABEL GIT_REF [these options]; compare two result "
               "files with scripts/bench/compare.py BEFORE.json AFTER.json (scripts/bench/README.md).",
    )
    ap.add_argument("--cases", type=Path, default=_env_path("BENCH_CASES"),
                    help="folder holding one photo folder per key case (default: $BENCH_CASES)")
    ap.add_argument("--key", type=Path, default=_env_path("BENCH_KEY"),
                    help="answer key (key.json, kept out of git) (default: $BENCH_KEY)")
    ap.add_argument("--out", type=Path, help="result JSON, rewritten after every run (with --rescore: optional)")
    ap.add_argument("--only", action="append", help="case name; repeatable")
    ap.add_argument("--repeat", type=_positive, default=1, metavar="N",
                    help="run every case N times (default 1); results give mean, min and max")
    ap.add_argument("--sort-cache", type=Path, metavar="DIR", default=_env_path("BENCH_SORT_CACHE"),
                    help="keep sorting results by photo sha256 (+ triage chain, SORT_PROMPT_VERSION, prompt "
                         "fingerprint and sorting thinking level); a cached photo costs no sorting call "
                         "(default: $BENCH_SORT_CACHE)")
    ap.add_argument("--reports", type=Path, metavar="DIR", default=_env_path("BENCH_REPORTS"),
                    help="folder with the CL report spreadsheets: scores drafted conditions and the Annex A prefill "
                         "builds, each case against its own report with the others as history (leave-one-out), "
                         "and flags drafted parts the report calls Serviceable (default: $BENCH_REPORTS)")
    ap.add_argument("--models", type=_chain, metavar="CHAIN", help="override the reading model chain (comma-separated)")
    ap.add_argument("--triage-models", type=_chain, metavar="CHAIN",
                    help="override the sorting model chain (comma-separated)")
    ap.add_argument("--second-look-models", type=_chain, metavar="CHAIN",
                    help="override the second look's model chain (comma-separated)")
    ap.add_argument("--no-second-look", action="store_true",
                    help="leave the second look out (the reading alone, as before it existed)")
    ap.add_argument("--thinking-level-read", metavar="LEVEL",
                    help="thinking level of the reading call: minimal, low, medium or high for every model, or per "
                         "model, e.g. 'gemini-3.5-flash-lite=medium' or 'low,gemini-3-flash-preview=high' "
                         "(settings.gemini_thinking_level_read; default: none sent)")
    ap.add_argument("--thinking-level-sort", metavar="LEVEL",
                    help="the same for the sorting calls (its own sort-cache folder)")
    ap.add_argument("--thinking-level-second-look", metavar="LEVEL", help="the same for the second look")
    ap.add_argument("--image-resolution", metavar="SPEC",
                    help="resolution of the reading call's photos by kind, e.g. "
                         "'damage=ultra_high,tyre=ultra_high,overview=medium' ('*' = every other kind; "
                         "low/medium/high/ultra_high; settings.gemini_image_resolution)")
    ap.add_argument("--read-tools", metavar="TOOLS",
                    help="tools for the reading call: 'code_execution' (Agentic Vision trial) for every model, or per "
                         "model, e.g. 'gemini-3.1-flash-lite=code_execution,gemini-3-flash-preview=code_execution' "
                         "(settings.gemini_read_tools)")
    # Contract 8 D1: the light reader looking closer.
    ap.add_argument("--read-tools-timeout", type=_seconds, metavar="SECONDS",
                    help="seconds per model try of a reading that carries tools (the reading's own 120 when not "
                         "given; settings.gemini_read_tools_timeout); needs --read-tools")
    ap.add_argument("--zoom-hint", action="store_true",
                    help="ask a model given the tool to crop and zoom into each damaged area "
                         "(extraction.ZOOM_NOTE; settings.gemini_read_zoom_hint); needs --read-tools")
    ap.add_argument("--sort-damage-box", action="store_true",
                    help="the sorter boxes the damage on each photo showing some (settings.intake_sort_damage_box; "
                         "its own sort-cache folder)")
    ap.add_argument("--read-crops", nargs="?", type=_positive, const=0, metavar="N",
                    help="a close-up of each box beside its photo, at most N per reading (default the setting's, 8; "
                         "settings.intake_read_crops / intake_read_crops_max); needs --sort-damage-box")
    ap.add_argument("--no-side-from-sorter", action="store_true",
                    help="ablation: the reader's sides stand (settings.intake_side_from_sorter off)")
    ap.add_argument("--no-sorter-hints", action="store_true",
                    help="ablation: no sorter's hint beside each photo (extraction.SORTER_HINTS off)")
    ap.add_argument("--max-read", type=int, metavar="N", help="photos one reading sends (settings.intake_max_read_photos)")
    ap.add_argument("--drop-rule", action="append", choices=sorted(READING_RULES),
                    help="ablation: take one reading rule out of the prompt (repeatable)")
    # Contract 8 D2: reading the car one area at a time.
    ap.add_argument("--read-mode", choices=("single", "zones"),
                    help="'zones': after the overall reading, one call per area of the car with that area's "
                         "checklist, merged in (settings.intake_read_mode; default single)")
    ap.add_argument("--grid-marks", action="store_const", const=True, default=None,
                    help="zone calls only: a 3x3 grid (A1..C3) drawn on each photo's copy, the model asked for the "
                         "cells the damage covers (settings.intake_grid_marks)")
    # Contract 9.
    ap.add_argument("--zone-models", type=_chain, metavar="CHAIN",
                    help="the zone calls' model chain (settings.gemini_zone_chain; default the two lites, never the "
                         "reading's); needs --read-mode zones")
    ap.add_argument("--zone-only", choices=("suggest", "list"),
                    help="the parts only zone reads found: 'suggest' (option B, the default) keeps them out of the "
                         "list as suggestions, scored like the second look's; 'list' puts them in the list "
                         "(settings.intake_zone_only); needs --read-mode zones")
    ap.add_argument("--store-on", action="store_true",
                    help="A/B only: the requests without store=False, as before contract 9 "
                         "(settings.gemini_store_off off)")
    ap.add_argument("--zone-evidence", metavar="MODE",
                    help="the zone reads' tail fields: 'reason', 'reason_box' or 'off' "
                         "(settings.intake_zone_evidence); needs --read-mode zones")
    ap.add_argument("--check", metavar="MODE",
                    help="re-check the suggestions: 'decision' (the Decisions API), 'lite', 'decision_then_lite' "
                         "or 'off' (settings.intake_check_mode; app/ai/check.py)")
    ap.add_argument("--decision-model", metavar="MODEL",
                    help="the Decisions API's model (settings.gemini_decision_model); needs --check")
    ap.add_argument("--check-models", type=_chain, metavar="CHAIN",
                    help="the lite re-check's chain (settings.gemini_check_chain); needs --check")
    ap.add_argument("--thinking-level-check", metavar="LEVEL",
                    help="the lite re-check's thinking level, written as --thinking-level-read "
                         "(settings.gemini_thinking_level_check); needs --check")
    ap.add_argument("--check-resolution", metavar="RES",
                    help="the photos' resolution on the Decisions API: low, medium, high or ultra_high "
                         "(settings.intake_check_resolution); needs --check")
    ap.add_argument("--check-yes", type=float, metavar="P",
                    help="p at or over which the Decisions API's answer is 'yes' (settings.intake_check_yes); "
                         "needs --check")
    ap.add_argument("--check-no", type=float, metavar="P",
                    help="p at or under which it is 'no' (settings.intake_check_no); needs --check")
    # Contract 10: the linked parts (knobs at the L1 freeze's defaults).
    ap.add_argument("--links", metavar="MODE",
                    help="the linked parts: 'off', 'shadow' (worked out, kept in the AI draft only) or 'on' (shown "
                         "with the suggestions) (settings.intake_links); scored apart from the other suggestions")
    ap.add_argument("--link-check", metavar="MODE",
                    help="the linked check: 'off' or 'lite' (settings.intake_link_check; app/ai/link_check.py); "
                         "needs --links on or shadow")
    ap.add_argument("--links-catalogue", action="store_true",
                    help="also the parts catalogue's diagram neighbours as candidates (settings."
                         "intake_links_catalogue; cache only); needs --links on or shadow")
    # Light-model detection (plans/2026-11-18-light-detection/P-prompts.md
    # section 4): one flag per experiment, each off by default.
    ap.add_argument("--read-packs", metavar="PACKS",
                    help="the main read's instruction packs, any of orient (R1), cues and small (R2), e.g. "
                         "'orient,small' (settings.intake_read_packs; app/ai/packs/)")
    ap.add_argument("--read-prompt", metavar="VERSION",
                    help="the main read's prompt: v1 (today's) or v2 (R3: damage last, walk A-E, a last text part "
                         "after the photos; settings.intake_read_prompt)")
    ap.add_argument("--read-anchor", action="store_true",
                    help="R4: the description's first sentence anchors the sides (settings.intake_read_anchor); "
                         "needs --read-prompt v2")
    ap.add_argument("--zone-packs", metavar="PACKS",
                    help="the zone reads' packs, any of small (Z1), intact (Z2), orient and cues (Z3) "
                         "(settings.intake_zone_packs); needs --read-mode zones")
    ap.add_argument("--zone-photo-half", action="store_true",
                    help="Z5: the end zones say which half of the photo each find is in, code works out the side "
                         "(settings.intake_zone_photo_half); needs --read-mode zones")
    ap.add_argument("--sort-prompt", metavar="VERSION",
                    help="the sort's prompt: v2 (today's) or v3 (S2; settings.intake_sort_prompt; its own sort-cache "
                         "folder). S1 is --thinking-level-sort high (a batch's try then gets 90 s)")
    ap.add_argument("--thinking-level-link", metavar="LEVEL",
                    help="L1: the linked check's thinking level, written as --thinking-level-read "
                         "(settings.gemini_thinking_level_link; at high its time box is 60 s); needs --link-check lite")
    ap.add_argument("--link-skill", metavar="VERSION",
                    help="the linked check's skill files: L1 (frozen) or L2 (worked examples; "
                         "settings.intake_link_skill); needs --link-check lite")
    ap.add_argument("--link-prompt", metavar="VERSION",
                    help="the linked check's base prompt: v1 (today's) or v2 (P's; settings.intake_link_prompt); "
                         "P's L2 arm is --link-skill L2 --link-prompt v2; needs --link-check lite")
    ap.add_argument("--link-signs", action="store_true",
                    help="L3: the signs answered in a field of their own, kept on link_run "
                         "(settings.intake_link_signs); needs --link-check lite and --link-skill L2")
    ap.add_argument("--link-order", metavar="ORDER",
                    help="L4: candidates_first (today) or photos_first, the closed list after the photos "
                         "(settings.intake_link_order); needs --link-check lite")
    # The assembly walk (plans/2026-11-18-car-knowledge/K1-design.md 5a).
    ap.add_argument("--assembly-walk", action="store_true",
                    help="each zone read also answers the car map's yes/no signs about its damaged anchor panels "
                         "(settings.intake_assembly_walk on; +0 calls); needs --read-mode zones and a car map with "
                         "signs (app/intake/car_map.json)")
    ap.add_argument("--walk-raise", metavar="HOW",
                    help="how far a yes sign lifts a listed part's hit: one (a step at most, the default) or full "
                         "(settings.intake_walk_raise); needs --assembly-walk and --links on or shadow")
    ap.add_argument("--link-hops", metavar="N",
                    help="1 (the default) or 2: a chain's second hop when a sign that unlocks it says yes, at most 4 "
                         "a case (settings.intake_link_hops); needs --assembly-walk and --links on or shadow")
    ap.add_argument("--keep-reads", action="store_true",
                    help="keep the overall reading's raw damage items, side_from_sorter's changes and each zone "
                         "read's items as merged (wraps the app's functions in this harness only; no extra call)")
    ap.add_argument("--rescore", type=Path, metavar="FILE",
                    help="score a stored result file again (strict and fair) with --key [and --reports]; offline, "
                         "no Gemini call, no database; --out writes the rescored file")
    ap.add_argument("--scratch", type=Path, metavar="DIR",
                    help="a throwaway folder for the run's database and storage (sets DATABASE_URL and "
                         "STORAGE_DIR; stands for BENCHMARK_SCRATCH=1) -- for a run on a PC")
    ap.add_argument("--label", help="name of this run in the result file (default: the --out file name)")
    ap.add_argument("--ref", help="git ref (and commit) the code came from, for the result file")
    ap.add_argument("--keep-photos", action="store_true",
                    help="keep each run's copied photos in STORAGE_DIR (default: deleted after scoring)")
    return ap.parse_args(argv)


# Contract 10's flags at their defaults: left out of a result file's "args",
# so a run without them writes the file it wrote before them.
C10_ARGS = {"links": None, "link_check": None, "links_catalogue": False, "keep_reads": False}
# The light-model flags at their defaults: left out of "args" the same way.
LIGHT_ARGS = {"read_packs": None, "read_prompt": None, "read_anchor": False, "zone_packs": None,
              "zone_photo_half": False, "sort_prompt": None, "thinking_level_link": None, "link_skill": None,
              "link_prompt": None, "link_signs": False, "link_order": None}
# The assembly walk's flags at their defaults: left out of "args" too.
WALK_ARGS = {"assembly_walk": False, "walk_raise": None, "link_hops": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scratch_env(folder: Path) -> dict:
    """--scratch DIR: the environment a run in that folder needs."""
    folder = Path(folder).resolve()
    return {
        "BENCHMARK_SCRATCH": "1",
        "DATABASE_URL": f"sqlite:///{(folder / 'run.db').as_posix()}",
        "STORAGE_DIR": str(folder / "storage"),
    }


def _use_scratch(folder: Path) -> None:
    """Points the app at --scratch DIR, before anything imports it:
    app.reports.db binds to DATABASE_URL on import. Already loaded on other
    paths (a test, an import before main), it refuses rather than run on
    whatever database that is."""
    env = scratch_env(folder)
    loaded = sys.modules.get("app.config")
    if loaded is not None and (loaded.settings.database_url, str(Path(loaded.settings.storage_dir))) != (
            env["DATABASE_URL"], env["STORAGE_DIR"]):
        sys.exit("--scratch: the app is already loaded with another database; run the script on its own.")
    Path(env["STORAGE_DIR"]).mkdir(parents=True, exist_ok=True)
    os.environ.update(env)


def model_settings(args: argparse.Namespace) -> dict:
    """The model-setting flags given, as {Settings field: normalised value}.
    Exits on a value config refuses, or a photo kind the sorter hasn't."""
    from app import config
    from app.ai.triage import PHOTO_KINDS

    out = {}
    for flag, field, normaliser in MODEL_SETTINGS:
        value = getattr(args, flag, None)
        if value is None:
            continue
        try:
            out[field] = getattr(config, normaliser)(value)
        except ValueError as exc:
            sys.exit(f"--{flag.replace('_', '-')}: {exc}")
    kinds = set(config.image_resolutions(out.get("gemini_image_resolution"))) - set(PHOTO_KINDS) - {"unsorted", config.ANY}
    if kinds:
        sys.exit(f"--image-resolution: no such photo kind {sorted(kinds)}; the kinds are "
                 f"{', '.join(list(PHOTO_KINDS) + ['unsorted'])}, or '*'")
    # Contract 8 D1. A flag that would change nothing stops the run: a
    # benchmark spent measuring the baseline under another name.
    tools = out.get("gemini_read_tools", config.settings.gemini_read_tools)
    for flag in ("read_tools_timeout", "zoom_hint"):
        if getattr(args, flag, None) not in (None, False) and not tools:
            sys.exit(f"--{flag.replace('_', '-')}: goes only with tools on the reading; give --read-tools too")
    if getattr(args, "read_tools_timeout", None) is not None:
        out["gemini_read_tools_timeout"] = args.read_tools_timeout
    if getattr(args, "zoom_hint", False):
        out["gemini_read_zoom_hint"] = True
    if getattr(args, "sort_damage_box", False):
        out["intake_sort_damage_box"] = True
    if getattr(args, "read_crops", None) is not None:
        if not (out.get("intake_sort_damage_box") or config.settings.intake_sort_damage_box):
            sys.exit("--read-crops: the close-ups are cut from the sorter's boxes; give --sort-damage-box too")
        out["intake_read_crops"] = True
        if args.read_crops:
            out["intake_read_crops_max"] = args.read_crops
    # Contract 9. Likewise: a zone flag without zone reading stops the run.
    zones_on = out.get("intake_read_mode", config.settings.intake_read_mode) == "zones"
    if getattr(args, "zone_models", None):
        if not zones_on:
            sys.exit("--zone-models: the zone calls' models; give --read-mode zones too")
        out["gemini_zone_chain"] = config.normal_chain(args.zone_models, "--zone-models")
    if getattr(args, "zone_only", None):
        if not zones_on:
            sys.exit("--zone-only: where the zone reads' finds go; give --read-mode zones too")
        out["intake_zone_only"] = config.normal_zone_only(args.zone_only)
    if getattr(args, "store_on", False):
        out["gemini_store_off"] = False
    out.update(check_settings(args, config, zones_on))
    out.update(links_settings(args, config))
    out.update(light_settings(args, config, zones_on, out))
    out.update(walk_settings(args, config, zones_on, out))
    return out


def light_settings(args: argparse.Namespace, config, zones_on: bool, given: dict) -> dict:
    """The light-model flags (P-prompts.md section 4) as {Settings field:
    value}. Exits on a value config refuses, or a flag that would change
    nothing (a benchmark spent measuring the baseline under another name):
    a zone flag without zone reads, a linked-check flag without the check,
    --read-anchor without v2, --link-signs without the L2 skills."""
    out: dict = {}

    def norm(flag: str, fn):
        try:
            return fn(getattr(args, flag))
        except ValueError as exc:
            sys.exit(f"--{flag.replace('_', '-')}: {exc}")

    def packs(choices):
        return lambda value: config.normal_packs(value, choices, "the packs")

    def one_of(choices):
        return lambda value: config._one_of(value, choices, "the value")

    if getattr(args, "read_packs", None) is not None:
        out["intake_read_packs"] = norm("read_packs", packs(config.READ_PACKS))
    if getattr(args, "read_prompt", None) is not None:
        out["intake_read_prompt"] = norm("read_prompt", one_of(config.READ_PROMPTS))
    if getattr(args, "read_anchor", False):
        out["intake_read_anchor"] = True
    if getattr(args, "zone_packs", None) is not None:
        if not zones_on:
            sys.exit("--zone-packs: the zone reads' packs; give --read-mode zones too")
        out["intake_zone_packs"] = norm("zone_packs", packs(config.ZONE_PACKS))
    if getattr(args, "zone_photo_half", False):
        if not zones_on:
            sys.exit("--zone-photo-half: the end zones' photo halves; give --read-mode zones too")
        out["intake_zone_photo_half"] = True
    if getattr(args, "sort_prompt", None) is not None:
        out["intake_sort_prompt"] = norm("sort_prompt", one_of(config.SORT_PROMPTS))
    check_on = given.get("intake_link_check", config.settings.intake_link_check) != "off"
    for flag, field, fn in (
        ("thinking_level_link", "gemini_thinking_level_link", config.normal_thinking_levels),
        ("link_skill", "intake_link_skill", config.normal_link_skill),
        ("link_prompt", "intake_link_prompt", one_of(config.LINK_PROMPTS)),
        ("link_order", "intake_link_order", one_of(config.LINK_ORDERS)),
    ):
        if getattr(args, flag, None) is None:
            continue
        if not check_on:
            sys.exit(f"--{flag.replace('_', '-')}: tunes the linked check; give --link-check lite too")
        out[field] = norm(flag, fn)
    if getattr(args, "link_signs", False):
        if not check_on:
            sys.exit("--link-signs: tunes the linked check; give --link-check lite too")
        out["intake_link_signs"] = True
    prompt = out.get("intake_read_prompt", config.settings.intake_read_prompt)
    if config.light_conflict(prompt, out.get("intake_read_anchor", config.settings.intake_read_anchor), "L1", False):
        sys.exit("--read-anchor: a part of read prompt v2; give --read-prompt v2 too")
    skill = out.get("intake_link_skill", config.settings.intake_link_skill)
    if config.light_conflict("v1", False, skill, out.get("intake_link_signs", config.settings.intake_link_signs)):
        sys.exit("--link-signs: uses the L2 skills' signs files; give --link-skill L2 too")
    return out


def walk_settings(args: argparse.Namespace, config, zones_on: bool, given: dict) -> dict:
    """The assembly walk's flags as {Settings field: value}. Exits on a
    value config refuses, or a flag that would change nothing: the walk
    without zone reads, --walk-raise / --link-hops without the walk or
    without the linked parts. (Whether a car map with signs loaded is
    main's check: it needs the app.)"""
    out: dict = {}
    walk = bool(getattr(args, "assembly_walk", False))
    if walk:
        if not zones_on:
            sys.exit("--assembly-walk: asked inside the zone reads; give --read-mode zones too")
        out["intake_assembly_walk"] = "on"
    links_on = given.get("intake_links", config.settings.intake_links) != "off"
    for flag, field, fn in (("walk_raise", "intake_walk_raise", config.normal_walk_raise),
                            ("link_hops", "intake_link_hops", config.normal_link_hops)):
        if getattr(args, flag, None) is None:
            continue
        name = f"--{flag.replace('_', '-')}"
        if not walk:
            sys.exit(f"{name}: tunes what the linked parts make of the walk; give --assembly-walk too")
        if not links_on:
            sys.exit(f"{name}: tunes the linked parts; give --links on or shadow too")
        try:
            out[field] = fn(getattr(args, flag))
        except ValueError as exc:
            sys.exit(f"{name}: {exc}")
    return out


def links_settings(args: argparse.Namespace, config) -> dict:
    """Contract 10's --links, --link-check and --links-catalogue as
    {Settings field: value}. Exits on a value config refuses, or a flag that
    would change nothing with the linked parts off. The knobs (cap, types,
    side, unknown hit, intact) stay at their defaults: frozen with the
    table (L1); scripts/bench/replay_links.py measures the pre-registered
    variants offline."""
    out: dict = {}
    mode = config.settings.intake_links
    if getattr(args, "links", None) is not None:
        try:
            mode = out["intake_links"] = config.normal_links_mode(args.links)
        except ValueError as exc:
            sys.exit(f"--links: {exc}")
    if getattr(args, "link_check", None) is not None:
        try:
            check = config.normal_link_check(args.link_check)
        except ValueError as exc:
            sys.exit(f"--link-check: {exc}")
        if check != "off" and mode == "off":
            sys.exit("--link-check: checks the linked parts; give --links on or shadow too")
        out["intake_link_check"] = check
    if getattr(args, "links_catalogue", False):
        if mode == "off":
            sys.exit("--links-catalogue: adds candidates to the linked parts; give --links on or shadow too")
        out["intake_links_catalogue"] = True
    return out


# Contract 9: the re-check's tuning flags -> (Settings field, config's
# normaliser). Each needs --check: alone it would change nothing.
CHECK_FLAGS = (
    ("decision_model", "gemini_decision_model", None),
    ("check_models", "gemini_check_chain", "normal_chain"),
    ("thinking_level_check", "gemini_thinking_level_check", "normal_thinking_levels"),
    ("check_resolution", "intake_check_resolution", "normal_check_resolution"),
)


def check_settings(args: argparse.Namespace, config, zones_on: bool) -> dict:
    """Contract 9's --zone-evidence, --check and the re-check's tuning, as
    {Settings field: value}. Exits on a value config refuses, or a flag
    that would change nothing (a benchmark spent measuring the baseline
    under another name)."""
    out: dict = {}
    if getattr(args, "zone_evidence", None) is not None:
        if not zones_on:
            sys.exit("--zone-evidence: the zone reads' reasons and boxes; give --read-mode zones too")
        try:
            out["intake_zone_evidence"] = config.normal_zone_evidence(args.zone_evidence)
        except ValueError as exc:
            sys.exit(f"--zone-evidence: {exc}")
    mode = config.settings.intake_check_mode
    if getattr(args, "check", None) is not None:
        try:
            mode = out["intake_check_mode"] = config.normal_check_mode(args.check)
        except ValueError as exc:
            sys.exit(f"--check: {exc}")
    for flag, field, normaliser in CHECK_FLAGS + (("check_yes", "intake_check_yes", None),
                                                  ("check_no", "intake_check_no", None)):
        value = getattr(args, flag, None)
        if value is None:
            continue
        if mode == "off":
            sys.exit(f"--{flag.replace('_', '-')}: tunes the re-check; give --check too")
        if flag == "decision_model":
            value = str(value).strip()
            if not value:
                sys.exit("--decision-model: a model name")
        elif flag in ("check_yes", "check_no"):
            if not 0.0 <= value <= 1.0:
                sys.exit(f"--{flag.replace('_', '-')}: a probability, 0 to 1")
        else:
            try:
                value = getattr(config, normaliser)(value)
            except ValueError as exc:
                sys.exit(f"--{flag.replace('_', '-')}: {exc}")
        out[field] = value
    yes = out.get("intake_check_yes", config.settings.intake_check_yes)
    no = out.get("intake_check_no", config.settings.intake_check_no)
    if no >= yes:
        sys.exit(f"--check-no {no} must be under --check-yes {yes}")
    return out


def ai_settings(settings) -> dict:
    """The model settings a run used, for the result file (None: unset), and
    contract 8 D1's while on."""
    out = {field.removeprefix("gemini_"): getattr(settings, field, None) for _, field, _ in MODEL_SETTINGS}
    for _, field in D1_SETTINGS:
        if getattr(settings, field, None):
            out[field.removeprefix("gemini_")] = getattr(settings, field)
    if getattr(settings, "intake_read_crops", False):
        out["intake_read_crops_max"] = settings.intake_read_crops_max
    # Contract 9's (--zone-models, --zone-only, --store-on) only while they
    # act -- the zone chain and zone_only in zone mode, store_off when turned
    # off -- so a run without them reads as before.
    if getattr(settings, "intake_read_mode", "single") == "zones":
        out["zone_chain"] = getattr(settings, "gemini_zone_chain", None)
        out["intake_zone_only"] = getattr(settings, "intake_zone_only", "suggest")
    if not getattr(settings, "gemini_store_off", True):
        out["store_off"] = False
    # Contract 9 (G): the zone reads' evidence in zone mode when asked for,
    # and the re-check's settings while it runs -- the Decisions API's for
    # its modes, the lite chain's for its.
    evidence = getattr(settings, "intake_zone_evidence", "off")
    if getattr(settings, "intake_read_mode", "single") == "zones" and evidence != "off":
        out["intake_zone_evidence"] = evidence
    mode = getattr(settings, "intake_check_mode", "off")
    if mode != "off":
        out["intake_check_mode"] = mode
        if mode in ("decision", "decision_then_lite"):
            out["decision_model"] = getattr(settings, "gemini_decision_model", None)
            out["intake_check_resolution"] = getattr(settings, "intake_check_resolution", None)
            out["intake_check_yes"] = getattr(settings, "intake_check_yes", None)
            out["intake_check_no"] = getattr(settings, "intake_check_no", None)
        if mode in ("lite", "decision_then_lite"):
            out["check_chain"] = getattr(settings, "gemini_check_chain", None)
            out["thinking_level_check"] = getattr(settings, "gemini_thinking_level_check", None)
    # Contract 10, while the linked parts act: their mode and knobs, the
    # link table's version and sha256 (as git keeps the file: the L1 freeze),
    # and the linked check's settings while it runs.
    links = links_mode(settings)
    if links != "off":
        out["intake_links"] = links
        for field in ("intake_links_max", "intake_links_types", "intake_links_side", "intake_links_unknown_hit",
                      "intake_links_intact"):
            out[field] = getattr(settings, field, None)
        if getattr(settings, "intake_links_catalogue", False):
            out["intake_links_catalogue"] = True
        out.update(link_table_settings())
        check = getattr(settings, "intake_link_check", "off")
        if check != "off":
            out["intake_link_check"] = check
            out["link_chain"] = getattr(settings, "gemini_link_chain", None)
            out["intake_link_check_max_calls"] = getattr(settings, "intake_link_check_max_calls", None)
            out["thinking_level_link"] = getattr(settings, "gemini_thinking_level_link", None)
    out.update(light_ai_settings(settings))
    out.update(walk_ai_settings(settings))
    return out


def walk_rows(walk, label_of: dict[str, str]) -> dict:
    """ai_draft["assembly_walk"] with each sign's photo by case label."""
    out = {}
    for panel, rec in walk.items() if isinstance(walk, dict) else ():
        if isinstance(rec, dict):
            signs = {sid: dict(got, photo=label_of.get(got.get("photo"), got.get("photo")))
                     for sid, got in (rec.get("signs") or {}).items() if isinstance(got, dict)}
            out[panel] = {"hit_at": rec.get("hit_at"), "signs": signs}
    return out


def walk_on(settings) -> bool:
    """The assembly walk acts: on, in zone mode."""
    return (getattr(settings, "intake_assembly_walk", "off") == "on"
            and getattr(settings, "intake_read_mode", "single") == "zones")


def walk_ai_settings(settings) -> dict:
    """The assembly walk's settings while it acts -- with the car map's
    version and sha256 (as git keeps the file) and, with the linked parts
    on, the raise and the hops; {} while off."""
    if not walk_on(settings):
        return {}
    from app.intake import car_map

    out = {"intake_assembly_walk": "on",
           "car_map_version": car_map.MAP.get("version") if isinstance(car_map.MAP, dict) else None,
           "car_map_sha256": car_map.sha256_lf(car_map.MAP_PATH) if isinstance(car_map.MAP, dict) else None}
    if links_mode(settings) != "off":
        out["intake_walk_raise"] = getattr(settings, "intake_walk_raise", "one")
        out["intake_link_hops"] = getattr(settings, "intake_link_hops", 1)
    return out


# The light-model settings (P-prompts.md) and their defaults: in ai_settings
# only while not at them, so a run without them reads as before.
LIGHT_DEFAULTS = {"intake_read_packs": "", "intake_read_prompt": "v1", "intake_read_anchor": False,
                  "intake_zone_packs": "", "intake_zone_photo_half": False, "intake_sort_prompt": "v2",
                  "intake_link_skill": "L1", "intake_link_prompt": "v1", "intake_link_signs": False,
                  "intake_link_order": "candidates_first"}
# The zone and linked-check ones only act with zone reads / the check on.
_LIGHT_ZONE = ("intake_zone_packs", "intake_zone_photo_half")
_LIGHT_LINK = ("intake_link_skill", "intake_link_prompt", "intake_link_signs", "intake_link_order")


def light_ai_settings(settings) -> dict:
    """The light-model settings that act and are not at their defaults,
    with the sha256 of every pack and template (app/ai/packs, app/ai/prompts)
    and, with the L2 skills, of theirs; {} at the defaults."""
    zones = getattr(settings, "intake_read_mode", "single") == "zones"
    check = links_mode(settings) != "off" and getattr(settings, "intake_link_check", "off") != "off"
    out = {}
    for field, default in LIGHT_DEFAULTS.items():
        value = getattr(settings, field, default)
        if value == default or (field in _LIGHT_ZONE and not zones) or (field in _LIGHT_LINK and not check):
            continue
        out[field] = value
    if not out:
        return {}
    from app.ai import prompt_packs

    out["packs_sha256"] = prompt_packs.packs_sha256()
    if out.get("intake_link_skill") == "L2":
        out["link_skills_l2_sha256"] = skills_sha256(SKILLS_DIR.parent / "skills-L2")
    return out


# The linked check's skill files (contract 10 section 4.4), frozen with the
# link table.
SKILLS_DIR = BOT_DIR / "app" / "ai" / "skills"


def lf_sha256(path) -> str | None:
    """sha256 of a text file as git keeps it, line endings LF: a Windows
    checkout (core.autocrlf) has CRLF on disk, the server's has LF, and the
    L1 freeze was taken of git's copy. None when it can't be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def skills_sha256(folder: Path = SKILLS_DIR) -> dict[str, str | None]:
    """{skill file name: lf_sha256} for every skill file."""
    return {p.name: lf_sha256(p) for p in sorted(Path(folder).glob("*.md"))}


def link_table_settings() -> dict:
    """The link table the app loaded: its version and lf_sha256 (both None
    when it didn't load), and one sha256 over the skill files' (name and
    sha, a line each)."""
    from app.intake import links

    skills = skills_sha256()
    joined = "".join(f"{name} {sha}\n" for name, sha in skills.items())
    return {"link_table_version": links.TABLE_VERSION,
            "link_table_sha256": lf_sha256(links.TABLE_PATH) if links.TABLE else None,
            "link_skills_sha256": hashlib.sha256(joined.encode()).hexdigest() if skills else None}


# The reading rules --drop-rule can take out of the prompt, by the words
# each starts with (app/ai/extraction.SYSTEM_PROMPT).
READING_RULES = {
    "shutlines": "   - Along the side of the car",
    "neighbours": "   - When a door is damaged",
    "tyre": "   - 'Wheel' is the rim",
    "regulator": "   - A bent or removed window regulator",
}


def _drop_rule(prompt: str, start: str) -> str:
    """The prompt without the one line that begins with start."""
    i = prompt.index(start)
    j = prompt.index("\n", i) + 1
    return prompt[:i] + prompt[j:]


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.rescore:
        return rescore(args)
    missing = [f"--{name}" for name in ("cases", "key", "out") if getattr(args, name) is None]
    if missing:
        sys.exit(f"needs {', '.join(missing)} (or --rescore FILE)")
    if args.scratch:
        _use_scratch(args.scratch)
    if os.environ.get("BENCHMARK_SCRATCH") != "1":
        sys.exit(REFUSAL)
    A = _app()
    for field, value in model_settings(args).items():
        setattr(A.settings, field, value)
    if args.models:
        A.settings.gemini_model_chain = args.models
    if args.triage_models:
        A.settings.gemini_triage_model_chain = args.triage_models
    if args.second_look_models:
        A.settings.gemini_second_look_chain = args.second_look_models
        # Measured even while it is off in the app (config.second_look_enabled).
        A.settings.second_look_enabled = True
    if args.no_second_look:
        A.settings.second_look_enabled = False
    # Ablation: one contract-6 piece off for this run, to see what it adds.
    import app.ai.extraction as extraction
    if args.no_side_from_sorter:
        A.settings.intake_side_from_sorter = False
    if args.no_sorter_hints:
        extraction.SORTER_HINTS = False
    if args.max_read:
        A.settings.intake_max_read_photos = args.max_read
    if args.drop_rule and A.settings.intake_read_prompt != "v1":
        sys.exit("--drop-rule: takes a rule out of read prompt v1; not with --read-prompt v2")
    for rule in args.drop_rule or []:
        extraction.SYSTEM_PROMPT = _drop_rule(extraction.SYSTEM_PROMPT, READING_RULES[rule])
    # Contract 10: a run "with links" on a table that didn't load would be
    # the baseline under another name (second_look._links_mode: off).
    if links_mode(A.settings) != "off" and _link_table() is None:
        sys.exit("--links: the link table (app/intake/link_table.json) didn't load; see the log")
    # The assembly walk with no car map that has signs would be skipped:
    # the baseline under another name.
    if walk_on(A.settings):
        from app.intake import zones as _zones

        if _zones.walk_index() is None:
            sys.exit("--assembly-walk: no car map with signs loaded (app/intake/car_map.json); see the log")
    if args.keep_reads:
        keep_reads_on(A)
    A.init_db()
    key = json.loads(args.key.read_text(encoding="utf-8"))
    unknown = sorted(set(args.only or []) - {c["name"] for c in key["cases"]})
    if unknown:
        sys.exit(f"--only: not in the key: {unknown}")
    cases = [c for c in key["cases"] if not args.only or c["name"] in args.only]

    reports = None
    if args.reports:
        reports = ReportSet(args.reports)
        print(f"reports: {len(reports.items)} read, {reports.skipped} skipped", flush=True)
        for case in cases:
            if reports.for_case(case) is None:
                print(f"   no report for {case['name']}: its conditions and Annex A are not scored", flush=True)
    cache, cache_meta = sort_cache_dir(args.sort_cache) if args.sort_cache else (None, None)

    doc = {
        "label": args.label or args.out.stem,
        "git_ref": args.ref,
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()
                 if not (k in C10_ARGS and v == C10_ARGS[k]) and not (k in LIGHT_ARGS and v == LIGHT_ARGS[k])
                 and not (k in WALK_ARGS and v == WALK_ARGS[k])},
        "models": {"model_chain": A.settings.gemini_model_chain, "triage_model_chain": A.settings.gemini_triage_model_chain,
                   "second_look_chain": A.settings.gemini_second_look_chain if A.settings.second_look_enabled else None},
        "ai_settings": ai_settings(A.settings),
        "sort_cache": cache_meta,
        "started": _now(),
        "finished": None,
        "runs": [],
        "summary": None,
    }
    for rep in range(1, args.repeat + 1):
        for case in cases:
            print(f"== {case['name']}" + (f"  (run {rep}/{args.repeat})" if args.repeat > 1 else ""), flush=True)
            r = run_case(case, args.cases, sort_cache=cache, reports=reports, keep_photos=args.keep_photos,
                         keep_reads=args.keep_reads)
            r["rep"] = rep
            doc["runs"].append(r)
            print(summary_line(r), flush=True)
            for line in detail_lines(r):
                print(line, flush=True)
            # After every run: a long run cut short keeps what it measured.
            doc["summary"] = summarize(doc["runs"])
            _write_json(args.out, doc)

    for line in total_lines(doc["runs"]):
        print(line)
    print()
    for line in summary_table(doc["summary"]):
        print(line)
    doc["finished"] = _now()
    _write_json(args.out, doc)
    print("written", args.out)
    return 0


def total_lines(runs: list[dict]) -> list[str]:
    """Per repetition: the strict TOTAL, with the checklist, with the second
    look (all ticked) and the fair score -- the console's totals."""
    reps = sorted({int(r.get("rep") or 1) for r in runs})
    out = []
    for rep in reps:
        totals = Counter()
        for r in runs:
            if int(r.get("rep") or 1) == rep and "parts" in r:
                for k in ("found", "expected", "visible_found", "visible_expected"):
                    totals[k] += r["parts"][k]
                    if r.get("parts_with_checklist"):
                        totals[f"checklist_{k}"] += r["parts_with_checklist"][k]
                totals["extra"] += len(r["parts"]["extra"])
                if r.get("parts_with_checklist"):
                    totals["checklist_runs"] += 1
                    totals["checklist_extra"] += len(r["parts_with_checklist"]["extra"])
                em = evidence_metrics(r)
                if "sug_real" in em:
                    totals["sug_runs"] += 1
                    for k in ("sug_n", "sug_real", "sug_wrong", "sug_box", "sug_reason", "chk_real_kept", "chk_wrong_no"):
                        totals[k] += em.get(k, 0)
                    totals["chk_runs"] += 1 if "chk_real_yes" in em else 0
                lm = link_metrics(r.get("links"))
                if lm:
                    totals["link_runs"] += 1
                    for k in ("link_shown", "link_real", "link_wrong_side", "link_extra", "link_strip_wrong", "lz_found",
                              "lzl_found", "lz_inside_found", "lzl_inside_found", "link_inside_expected"):
                        totals[k] += lm[k]
                ls = r.get("parts_with_second_look")
                if ls:
                    totals["look_runs"] += 1
                    for k in ("found", "visible_found", "added", "extra", "wrong_side", "suggested"):
                        totals[f"look_{k}"] += ls[k]
                f = r.get("parts_fair")
                if f:
                    totals["fair_runs"] += 1
                    for k in ("fair_found", "fair_visible_found", "fair_extra"):
                        totals[k] += f[k]
                    totals["neighbour"] += len(f["neighbour"])
                    totals["other_named"] += len(f["other_named"])
                    if f.get("contradicted") is not None:
                        totals["contradicted_runs"] += 1
                        totals["contradicted"] += len(f["contradicted"])
        head = f"run {rep}: " if len(reps) > 1 else ""
        out.append(f"\n{head}TOTAL parts {totals['found']}/{totals['expected']}, visible {totals['visible_found']}/{totals['visible_expected']}, "
                   f"extra {totals['extra']}")
        if totals["checklist_runs"]:
            out.append(f"{head}WITH CHECKLIST parts {totals['checklist_found']}/{totals['checklist_expected']}, "
                       f"visible {totals['checklist_visible_found']}/{totals['checklist_visible_expected']}, extra {totals['checklist_extra']}")
        if totals["look_runs"]:
            out.append(f"{head}WITH SECOND LOOK (all ticked) parts {totals['look_found']}/{totals['expected']}, "
                       f"visible {totals['look_visible_found']}/{totals['visible_expected']}: added {totals['look_added']} "
                       f"from {totals['look_suggested']} suggestions, extra {totals['look_extra']}, "
                       f"wrong side {totals['look_wrong_side']}")
        if totals["sug_runs"]:
            out.append(f"{head}SUGGESTIONS {totals['sug_n']}: real {totals['sug_real']}, wrong {totals['sug_wrong']}; "
                       f"AI box {totals['sug_box']}, reason {totals['sug_reason']}"
                       + (f"; RE-CHECK kept {totals['chk_real_kept']} of the real, said no to {totals['chk_wrong_no']} "
                          f"of the wrong" if totals["chk_runs"] else ""))
        if totals["link_runs"]:
            out.append(f"{head}LINKED PARTS {totals['link_shown']}: real {totals['link_real']}, wrong side "
                       f"{totals['link_wrong_side']}, extra {totals['link_extra']} (strip-down wrong "
                       f"{totals['link_strip_wrong']}); if all ticked parts {totals['lz_found']} -> {totals['lzl_found']}"
                       f"/{totals['expected']}, inside {totals['lz_inside_found']} -> {totals['lzl_inside_found']}"
                       f"/{totals['link_inside_expected']}")
        if totals["fair_runs"]:
            out.append(f"{head}FAIR parts {totals['fair_found']:g}/{totals['expected']}, visible "
                       f"{totals['fair_visible_found']:g}/{totals['visible_expected']} ({totals['neighbour']} neighbours "
                       f"at 0.5), extra {totals['fair_extra']} (strict {totals['extra']}, Other named like a key part "
                       f"{totals['other_named']})"
                       + (f", contradicted by the report {totals['contradicted']}" if totals["contradicted_runs"] else ""))
    return out


def rescore(args: argparse.Namespace) -> int:
    """--rescore FILE: every run of a stored result file scored again, strict
    and fair, from its drafted items (drafted_parts in files older than
    them), with the found parts' detail (score_details), against --key and,
    with --reports, each case's held-out report (contradicted parts,
    Replace / Repair). Offline: no Gemini call, no database. The other
    numbers (conditions, Annex A, sorter) are kept as stored -- except
    Annex A when --reports comes with a scratch database (--scratch DIR, or
    BENCHMARK_SCRATCH=1 with DATABASE_URL pointing at one): prefill builds
    it again from the drafted items, history = the other reports, and its
    lines and prices are scored as a run scores them (prefill makes no
    Gemini call either). Prints the totals and the summary; --out writes
    the rescored file (never over FILE, a measurement that can't be made
    again)."""
    if args.key is None:
        sys.exit("--rescore needs --key")
    if args.out is not None and args.out.resolve() == args.rescore.resolve():
        sys.exit("--rescore: --out must be another file; the stored result is kept as it is")
    annex = args.reports is not None and (args.scratch is not None or os.environ.get("BENCHMARK_SCRATCH") == "1")
    if annex and args.scratch is not None:
        _use_scratch(args.scratch)  # before anything imports the app's database
    doc = json.loads(args.rescore.read_text(encoding="utf-8"))
    runs = doc.get("runs")
    if runs is None:
        runs = doc.get("results") or []  # before --repeat
    key = {c["name"]: c for c in json.loads(args.key.read_text(encoding="utf-8"))["cases"]}
    reports = ReportSet(args.reports) if args.reports else None
    history = None
    if reports is not None:
        from app.parts import history
    if annex:
        _app().init_db()
    changed, unknown, rebuilt = 0, set(), 0
    for run in runs:
        case = key.get(run.get("case"))
        if case is None:
            unknown.add(str(run.get("case")))
            continue
        if "parts" not in run:
            continue  # an errored run: nothing was drafted
        items = run.get("drafted_items") or [{"part": p} for p in run.get("drafted_parts") or [] if p]
        drafted = [p for p in run.get("drafted_parts") or [i.get("part") for i in items] if p]
        strict = score_parts(case["parts"], drafted)
        if (strict["found"], len(strict["extra"])) != (run["parts"]["found"], len(run["parts"]["extra"])):
            changed += 1
        run["parts"] = strict
        contradicted = None
        held = reports.for_case(case) if reports is not None else None
        if held is not None:
            contradicted = contradicted_parts(drafted, held[1].lines, history.family_of, history.side_of,
                                              history.family_and_side)
        run["parts_fair"] = score_fair(case["parts"], items, strict, contradicted)
        if isinstance(run.get("suggestions"), list):
            mark_truth(run["suggestions"], case["parts"], strict)
        if isinstance(run.get("links"), dict) and isinstance(run.get("link_suggestions"), list):
            # Contract 10: the linked parts on this key, as the run scored them.
            kept = {k: run["links"][k] for k in ("mode", "zone_joined", "impact") if k in run["links"]}
            run["links"] = score_links(case["parts"], drafted, [r.get("part") for r in run.get("suggestions") or []],
                                       run["link_suggestions"], inside=hidden_children(_link_table()))
            run["links"].update(kept)
        score_details(run, case, held[1].lines if held is not None else None,
                      history.normalise_item if history else None, history.side_of if history else None)
        if annex and held is not None:
            load_history(reports.others(held))
            run["annex_a"] = score_annex_a(held[1], items)
            rebuilt += 1
    doc["runs"] = runs
    doc.pop("results", None)
    doc["summary"] = summarize(runs)
    doc["rescored"] = {"from": str(args.rescore), "key": str(args.key), "reports": str(args.reports) if args.reports else None,
                       "annex_a_rebuilt": rebuilt, "at": _now()}
    print(f"rescored {len(runs)} run(s) of {doc.get('label') or args.rescore.stem}"
          + (f"; reports: {len(reports.items)} read" if reports is not None else "; no --reports: nothing flagged as contradicted"))
    if unknown:
        print(f"   not in the key, left as stored: {len(unknown)} case(s)")
    if changed:
        print(f"   the strict score differs from the stored one on {changed} run(s): the key changed since")
    if annex:
        print(f"   Annex A built again on the scratch database for {rebuilt} run(s), prices scored")
    elif reports is not None:
        print("   Annex A kept as stored (with --scratch DIR it is built again and its prices scored)")
    for line in total_lines(runs):
        print(line)
    print()
    for line in summary_table(doc["summary"]):
        print(line)
    if args.out is not None:
        _write_json(args.out, doc)
        print("written", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
