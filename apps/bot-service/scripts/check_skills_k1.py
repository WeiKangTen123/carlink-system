"""Checks the car map K1.0 (app/intake/car_map.json) against L1 and the
surveyor skill files v2 written from it (app/ai/skills-K1/*.md).
Format: plans/2026-11-18-car-knowledge/K1-design.md (section 5c).

    python scripts/check_skills_k1.py            # exit 1 on any problem

The map:
  - car_map.validate() finds nothing;
  - every L1 link (link_table.json) is a relation with the same id, and
    every L1 field is as L1 has it except sources, which may only gain
    entries (a change would need its own line in CHANGED, with a reason);
  - every prior names a K2-notes row ("K2 A01: ..."), never a case;
  - no catalogue numbers (oem_numbers stays empty until the owner rules
    on licensed numbering).
The skill files (one per area; left and right share side.md):
  - the header "skill: <area>  version: <map version>", the five
    sections in order (What to look at, Linked parts, Signs of hidden
    damage, How to decide, Never), at most MAX_WORDS words, and the
    Never rules (candidate list, side, outside this area, "near");
  - every relation of links_table(map, names=True) is listed under its
    parent in its area's file with the same type, level, trigger words,
    hidden / strip-down flags, position, "optional" (the child is
    if_fitted), body words, and the ids of exactly the signs pointing at
    it -- and the file lists nothing else;
  - every sign is listed, with its level and its question word for word,
    in the file of each area it points into (the areas of the relations it
    points at and of the chains it unlocks), and nowhere else;
  - every chain is listed with its level and the signs that open it in
    the file of its first relation's area, and nowhere else.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # apps/bot-service
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="check-skills-k1-")
os.environ.setdefault("STORAGE_DIR", _tmp)
os.environ.setdefault("SECRETS_DIR", str(Path(_tmp, "secrets")))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp, 'check.db').as_posix()}")
os.environ.setdefault("GEMINI_API_KEY", "fake-check-skills-k1")
os.environ.setdefault("RUN_BACKGROUND_CLEANUP", "false")

from app.intake import car_map  # noqa: E402

MAP_PATH = ROOT / "app" / "intake" / "car_map.json"
L1_PATH = ROOT / "app" / "intake" / "link_table.json"
SKILLS = ROOT / "app" / "ai" / "skills-K1"
FILES = ("front", "rear", "side", "underbody", "wheel")
FILE_OF = {"front": "front", "rear": "rear", "side": "side", "left": "side", "right": "side",
           "underbody": "underbody", "wheel": "wheel"}
SECTIONS = ["## What to look at", "## Linked parts", "## Signs of hidden damage", "## How to decide", "## Never"]
NEVER_RULES = {
    "candidate list": r"not on the candidate list",
    "side": r"never decide a side",
    "outside this area": r"outside this area",
    "near": r"\"near\"",
}
MAX_WORDS = 1450  # the brief's "<= ~1,400"
L1_FIELDS = ("parent", "child", "type", "side", "min_hit", "trigger_tokens", "hidden", "strip_down", "area", "reason")
# L1 links whose L1 fields K1.0 changes, with the reason (K1b report). None today.
CHANGED: dict[str, str] = {}
# Body gates as the skill files word them (car_map.json uses these lists).
TAILGATE_GLASS = ["Hatchback", "SUV", "MPV", "Van", "Wagon"]
NOT_PICKUP = ["sedan", "Hatchback", "SUV", "MPV", "Van", "Wagon"]

PARENT_RE = re.compile(r"^\*\*(?P<parent>[^*]+)\*\*\s*$")
BULLET_RE = re.compile(r"^- (?P<child>[^\[]+?) \[(?P<attrs>[^\]]+)\](?: (?P<signs>[a-z][a-z0-9-]*(?:, [a-z][a-z0-9-]*)*))?$")
CHAIN_RE = re.compile(r"^- (?P<id>[a-z0-9][a-z0-9._-]*>[a-z0-9>._-]+) \((?P<level>[a-z]+)\): (?P<signs>.+)$")
SIGN_RE = re.compile(r"^- (?P<id>[a-z][a-z0-9-]*) \((?P<level>[a-z]+)\): (?P<ask>.+)$")


def gate_words(gate) -> str | None:
    """A body gate as a skill line's flag. A pickup's missing boot parts
    say nothing: code drops those links on a known pickup."""
    if not gate or gate == NOT_PICKUP:
        return None
    if gate in (["hatch"], TAILGATE_GLASS):
        return "tailgate only"
    return "only " + "/".join(gate)


def check_map(cm: dict, l1: dict, errors: list[str]) -> None:
    errors += [f"map: {e}" for e in car_map.validate(cm)]
    rels = {r.get("id"): r for r in cm.get("relations") or [] if isinstance(r, dict)}
    for link in l1["links"]:
        rel = rels.get(link["id"])
        if rel is None:
            errors.append(f"map: L1 link {link['id']!r} is gone")
            continue
        changed = [f for f in L1_FIELDS if rel.get(f) != link[f]]
        if changed and link["id"] not in CHANGED:
            errors.append(f"map: L1 link {link['id']!r} changes {', '.join(changed)} with no line in CHANGED")
        if not set(link["sources"]) <= set(rel.get("sources") or []):
            errors.append(f"map: L1 link {link['id']!r} loses a source")
    for rel in rels.values():
        prior = rel.get("prior")
        if prior is not None and not re.match(r"^K2 A\d{2}: (often|sometimes|rare)\b", str(prior.get("source"))):
            errors.append(f"map: relation {rel.get('id')!r}: a prior must name its K2-notes row")
    for asm in cm.get("assemblies") or []:
        if (asm.get("catalogue") or {}).get("oem_numbers"):
            errors.append(f"map: assembly {asm.get('id')!r} carries catalogue numbers (keep them out of the repo)")


def expected(cm: dict) -> tuple[dict, dict, dict]:
    """({file: {(parent, child): fields}}, {file: {sign id: (level, ask)}},
    {file: {chain id: (level, sign ids)}}) as the map says."""
    table = {link["id"]: link for link in car_map.links_table(cm, names=True)["links"]}
    by_pair, first = {}, {}
    for asm in cm["assemblies"]:
        for m in asm["members"]:
            by_pair[(asm["id"], m["part"])] = m
            first.setdefault(m["part"], m)
    pointing: dict[str, list[str]] = {}
    opening: dict[str, list[str]] = {}
    for asm in cm["assemblies"]:
        for s in asm.get("signs") or []:
            for rid in s.get("points_to") or []:
                pointing.setdefault(rid, []).append(s["id"])
            for cid in s.get("unlocks") or []:
                opening.setdefault(cid, []).append(s["id"])
    rels: dict[str, dict] = {f: {} for f in FILES}
    for rel in cm["relations"]:
        link = table[rel["id"]]
        child = by_pair.get((rel["assembly"], rel["child"])) or first.get(rel["child"]) or {}
        key = (car_map.base(link["parent"]), car_map.base(link["child"]))
        rels[FILE_OF[link["area"]]][key] = {
            "type": link["type"], "level": link["min_hit"], "triggers": set(link["trigger_tokens"]),
            "hidden": link["hidden"], "strip_down": link["strip_down"], "position": rel.get("position", "any"),
            "optional": child.get("fitted") == "if_fitted", "gate": gate_words(rel.get("body") or child.get("body")),
            "signs": set(pointing.get(rel["id"], [])), "id": rel["id"],
        }
    area_of = {r["id"]: FILE_OF[table[r["id"]]["area"]] for r in cm["relations"]}
    chains = {c["id"]: c for c in cm.get("chains") or []}
    signs: dict[str, dict] = {f: {} for f in FILES}
    for asm in cm["assemblies"]:
        for s in asm.get("signs") or []:
            files = {area_of[rid] for rid in s.get("points_to") or []}
            files |= {area_of[chains[cid]["first"]] for cid in s.get("unlocks") or []}
            for f in files:
                signs[f][s["id"]] = (s["level"], s["ask"])
    chain_files: dict[str, dict] = {f: {} for f in FILES}
    for c in chains.values():
        chain_files[area_of[c["first"]]][c["id"]] = (c.get("min_hit", "heavy"), set(opening.get(c["id"], [])))
    return rels, signs, chain_files


def parse(area: str, text: str, version: str, errors: list[str]) -> tuple[dict, dict, dict]:
    where = f"skills-K1/{area}.md"
    lines = text.splitlines()
    header = f"skill: {area}  version: {version}"
    if not lines or lines[0].strip() != header:
        errors.append(f"{where}: the first line should be {header!r}")
    pos = [next((i for i, line in enumerate(lines) if line.strip() == s), -1) for s in SECTIONS]
    if -1 in pos:
        errors.append(f"{where}: missing sections {[s for s, p in zip(SECTIONS, pos) if p == -1]}")
        return {}, {}, {}
    if pos != sorted(pos):
        errors.append(f"{where}: sections out of order")
    words = len(text.split())
    if words > MAX_WORDS:
        errors.append(f"{where}: {words} words (at most about 1,400)")
    never = "\n".join(lines[pos[4]:]).lower()
    for rule, pattern in NEVER_RULES.items():
        if not re.search(pattern, never):
            errors.append(f"{where}: the Never section lacks the {rule!r} rule")
    rels: dict = {}
    chains: dict = {}
    parent = None
    for line in lines[pos[1] + 1:pos[2]]:
        line = line.strip()
        if m := PARENT_RE.match(line):
            parent = m.group("parent")
        elif m := CHAIN_RE.match(line):
            if m.group("id") in chains:
                errors.append(f"{where}: chain {m.group('id')} listed twice")
            chains[m.group("id")] = (m.group("level"), set(m.group("signs").split(", ")))
        elif m := BULLET_RE.match(line):
            if parent is None:
                errors.append(f"{where}: a part before any parent: {line}")
                continue
            attrs = [a.strip() for a in m.group("attrs").split(",")]
            if len(attrs) < 2:
                errors.append(f"{where}: {line}: needs [type, level, ...]")
                continue
            got = {"type": attrs[0], "level": attrs[1], "triggers": set(), "hidden": False, "strip_down": False,
                   "position": "any", "optional": False, "gate": None,
                   "signs": set((m.group("signs") or "").split(", ")) - {""}}
            for flag in attrs[2:]:
                if flag == "hidden":
                    got["hidden"] = True
                elif flag == "strip-down":
                    got["strip_down"] = True
                elif flag in ("centre", "corner"):
                    got["position"] = flag
                elif flag == "optional":
                    got["optional"] = True
                elif flag.startswith("any if "):
                    got["triggers"] = set(flag[len("any if "):].split("/"))
                elif flag == "tailgate only" or flag.startswith("only "):
                    got["gate"] = flag
                else:
                    errors.append(f"{where}: {line}: unknown flag {flag!r}")
            key = (parent, m.group("child").strip())
            if key in rels:
                errors.append(f"{where}: {key[0]} -> {key[1]} listed twice")
            rels[key] = got
        elif line.startswith("- "):
            errors.append(f"{where}: a Linked parts line the check can't read: {line}")
    signs: dict = {}
    for line in lines[pos[2] + 1:pos[3]]:
        line = line.strip()
        if m := SIGN_RE.match(line):
            if m.group("id") in signs:
                errors.append(f"{where}: sign {m.group('id')} listed twice")
            signs[m.group("id")] = (m.group("level"), m.group("ask"))
        elif line.startswith("- "):
            errors.append(f"{where}: a sign line the check can't read: {line}")
    return rels, signs, chains


def main() -> int:
    errors: list[str] = []
    cm = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    l1 = json.loads(L1_PATH.read_text(encoding="utf-8"))
    check_map(cm, l1, errors)
    if errors:
        for e in errors:
            print("PROBLEM", e)
        print(f"{len(errors)} problem(s)")
        return 1
    want_rels, want_signs, want_chains = expected(cm)
    named_signs: set[str] = set()
    for area in FILES:
        path = SKILLS / f"{area}.md"
        where = f"skills-K1/{area}.md"
        if not path.exists():
            errors.append(f"{where} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        rels, signs, chains = parse(area, text, cm["version"], errors)
        for key, want in want_rels[area].items():
            got = rels.get(key)
            if got is None:
                errors.append(f"{where}: {want['id']} ({key[0]} -> {key[1]}) is not listed under its parent")
                continue
            for field in ("type", "level", "triggers", "hidden", "strip_down", "position", "optional", "gate", "signs"):
                if got[field] != want[field]:
                    errors.append(f"{where}: {key[0]} -> {key[1]}: {field} is {got[field]!r}, the map says {want[field]!r}")
        for key in rels:
            if key not in want_rels[area]:
                errors.append(f"{where}: {key[0]} -> {key[1]} is no relation of the map in this area")
        for sid, want in want_signs[area].items():
            if signs.get(sid) != want:
                errors.append(f"{where}: sign {sid} is {signs.get(sid)!r}, the map says {want!r}")
        for sid in signs:
            if sid not in want_signs[area]:
                errors.append(f"{where}: sign {sid} points at nothing in this area (or is no sign of the map)")
        named_signs |= set(signs)
        for cid, want in want_chains[area].items():
            if chains.get(cid) != want:
                errors.append(f"{where}: chain {cid} is {chains.get(cid)!r}, the map says {want!r}")
        for cid in chains:
            if cid not in want_chains[area]:
                errors.append(f"{where}: chain {cid} does not start in this area (or is no chain of the map)")
        print(f"{area:10s} relations {len(want_rels[area]):3d}  signs {len(want_signs[area]):3d}  "
              f"chains {len(want_chains[area]):2d}  words {len(text.split()):5d}  sha256 (LF) {car_map.sha256_lf(path)}")
    all_signs = {s["id"] for a in cm["assemblies"] for s in a.get("signs") or []}
    for sid in sorted(all_signs - named_signs):
        errors.append(f"sign {sid} is in no skill file")
    print(f"car_map.json {cm['version']}: {len(cm['relations'])} relations, {len(all_signs)} signs, "
          f"{len(cm.get('chains') or [])} chains; sha256 (LF) {car_map.sha256_lf(MAP_PATH)}")
    for e in errors:
        print("PROBLEM", e)
    print("ok" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
