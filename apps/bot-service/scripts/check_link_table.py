"""Checks app/intake/link_table.json and the surveyor skill files in app/ai/skills/.

Run from anywhere: python scripts/check_link_table.py  (exit 1 on any problem).

The table:
  - every parent and child, after {side} expands to Left and Right, is in
    taxonomy.CANONICAL_PARTS (no "(side undetermined)" names, no Other);
  - unique ids and unique parent/child pairs;
  - the side field matches where {side} is ("same": both sided; "impact":
    only the child; "none": the child unsided), so {side} links are symmetric;
  - fields and enums as the brief has them; trigger tokens are firm condition
    words; reasons <= 160 characters with no number of 3+ digits; sources
    "C x.y" or "oem: DIAGRAM", with no make or model name.
The skill files (one per area):
  - the header line, the five sections in order, about 1,200 words at most;
  - the Never rules (candidate list, side, outside this area, "near");
  - every table link appears under its parent in its area's file, with the
    same type, level, triggers, hidden and strip-down flags, and the file has
    no link the table hasn't.

taxonomy.py is loaded from its file so the app package (and its settings)
is never imported.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # apps/bot-service
TABLE = ROOT / "app" / "intake" / "link_table.json"
SKILLS = ROOT / "app" / "ai" / "skills"
TAXONOMY = ROOT / "app" / "reports" / "taxonomy.py"

TYPES = {"attached", "behind", "opens_with", "crush"}
SIDES = {"impact", "none", "same"}
HITS = ["light", "medium", "heavy", "crush"]
AREAS = ["front", "rear", "side", "underbody", "wheel"]
FIELDS = ["id", "parent", "child", "type", "side", "min_hit", "trigger_tokens", "hidden", "strip_down", "area",
          "reason", "sources"]
SECTIONS = ["## What to look at", "## Linked parts", "## Signs of hidden damage", "## How to decide", "## Never"]
NEVER_RULES = {
    "candidate list": r"not on the candidate list",
    "side": r"never decide a side",
    "outside this area": r"outside this area",
    "near": r"\"near\"",
}
MAX_WORDS = 1300  # the brief's "<= ~1,200"
# "C 4.1" (a C-collision-knowledge section) or "oem: TRUNK LID" (a diagram-pairs
# diagram; "A > B (next diagram)" for neighbouring diagrams).
SOURCE_RE = re.compile(r"^(C \d+(\.\d+)?|oem(: [A-Z0-9 /().>'-]+( \(next diagram\))?)?)$")
# The catalogue cars diagram-pairs read: their names must not leak into the data.
MAKE_WORDS = re.compile(r"\b(honda|civic|hr-v|bmw|x1)\b", re.I)
SIDE = "{side}"


def load_taxonomy():
    spec = importlib.util.spec_from_file_location("carlink_taxonomy", TAXONOMY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expand(name: str) -> list[str]:
    return [name.replace(SIDE, s) for s in ("Left", "Right")] if SIDE in name else [name]


def base(name: str) -> str:
    return name.replace(SIDE + " ", "")


def check_table(data: dict, taxonomy, errors: list[str]) -> list[dict]:
    canonical = set(taxonomy.CANONICAL_PARTS)
    tokens = set(taxonomy.FIRM_CONDITION_TOKENS)
    if not isinstance(data.get("version"), str) or not data["version"]:
        errors.append("table: no version")
    if not isinstance(data.get("built_from"), list):
        errors.append("table: built_from is not a list")
    links = data.get("links")
    if not isinstance(links, list) or not links:
        errors.append("table: no links")
        return []
    ids, pairs = set(), set()
    for n, link in enumerate(links):
        where = f"link {n} ({link.get('id', '?')})"
        missing = [f for f in FIELDS if f not in link]
        extra = [f for f in link if f not in FIELDS]
        if missing or extra:
            errors.append(f"{where}: missing {missing} extra {extra}")
            continue
        if link["id"] in ids:
            errors.append(f"{where}: duplicate id")
        ids.add(link["id"])
        pair = (link["parent"], link["child"])
        if pair in pairs:
            errors.append(f"{where}: duplicate parent/child pair")
        pairs.add(pair)
        if link["parent"] == link["child"]:
            errors.append(f"{where}: parent is its own child")
        for key in ("parent", "child"):
            for name in expand(link[key]):
                if name not in canonical:
                    errors.append(f"{where}: {key} {name!r} is not in CANONICAL_PARTS")
                if name.endswith(taxonomy.UNDETERMINED_SUFFIX) or name == taxonomy.OTHER_PART:
                    errors.append(f"{where}: {key} {name!r} is an undetermined/other name")
        p_sided, c_sided = SIDE in link["parent"], SIDE in link["child"]
        want = "same" if p_sided and c_sided else "impact" if c_sided else "none"
        if link["side"] != want:
            errors.append(f"{where}: side {link['side']!r}, the names say {want!r}")
        if link["type"] not in TYPES:
            errors.append(f"{where}: type {link['type']!r}")
        if link["side"] not in SIDES:
            errors.append(f"{where}: side {link['side']!r}")
        if link["min_hit"] not in HITS:
            errors.append(f"{where}: min_hit {link['min_hit']!r}")
        if link["area"] not in AREAS:
            errors.append(f"{where}: area {link['area']!r}")
        for flag in ("hidden", "strip_down"):
            if not isinstance(link[flag], bool):
                errors.append(f"{where}: {flag} is not true/false")
        if not isinstance(link["trigger_tokens"], list) or any(t not in tokens for t in link["trigger_tokens"]):
            errors.append(f"{where}: trigger_tokens {link['trigger_tokens']!r} are not all firm condition words")
        reason = link["reason"]
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{where}: no reason")
        else:
            if len(reason) > 160:
                errors.append(f"{where}: reason is {len(reason)} characters (max 160)")
            if re.search(r"\d{3,}", reason):
                errors.append(f"{where}: reason has a number of 3+ digits")
        sources = link["sources"]
        if not isinstance(sources, list) or not sources or any(not SOURCE_RE.match(str(s)) for s in sources):
            errors.append(f"{where}: sources {sources!r}")
        elif MAKE_WORDS.search(" ".join(sources) + " " + str(reason)):
            errors.append(f"{where}: a make or model name in reason or sources")
    return links


BULLET_RE = re.compile(r"^- (?P<child>[^\[]+?) \[(?P<attrs>[^\]]+)\]")
PARENT_RE = re.compile(r"^\*\*(?P<parent>.+?)\*\*\s*$")


def parse_skill(area: str, text: str, version: str, errors: list[str]) -> dict:
    """{(parent, child): (type, hit, triggers, hidden, strip_down)} from the
    file's Linked parts section."""
    where = f"skills/{area}.md"
    lines = text.splitlines()
    want_header = f"skill: {area}  version: {version}"
    if not lines or lines[0].strip() != want_header:
        errors.append(f"{where}: first line should be {want_header!r}")
    positions = [next((i for i, l in enumerate(lines) if l.strip() == s), -1) for s in SECTIONS]
    if -1 in positions:
        errors.append(f"{where}: missing sections {[s for s, p in zip(SECTIONS, positions) if p == -1]}")
        return {}
    if positions != sorted(positions):
        errors.append(f"{where}: sections out of order")
    words = len(text.split())
    if words > MAX_WORDS:
        errors.append(f"{where}: {words} words (max about 1,200)")
    never = "\n".join(lines[positions[4]:]).lower()
    for rule, pattern in NEVER_RULES.items():
        if not re.search(pattern, never):
            errors.append(f"{where}: Never section lacks the {rule!r} rule")
    found: dict = {}
    parent = None
    for line in lines[positions[1] + 1:positions[2]]:
        m = PARENT_RE.match(line.strip())
        if m:
            parent = m.group("parent")
            continue
        m = BULLET_RE.match(line.strip())
        if not m:
            continue
        if parent is None:
            errors.append(f"{where}: bullet before any parent: {line.strip()}")
            continue
        attrs = [a.strip() for a in m.group("attrs").split(",")]
        if len(attrs) < 2:
            errors.append(f"{where}: {line.strip()}: needs [type, level, ...]")
            continue
        triggers: set = set()
        hidden = strip = False
        for flag in attrs[2:]:
            if flag == "hidden":
                hidden = True
            elif flag == "strip-down":
                strip = True
            elif flag.startswith("any level if "):
                triggers = set(flag[len("any level if "):].split("/"))
            else:
                errors.append(f"{where}: {line.strip()}: unknown flag {flag!r}")
        key = (parent, m.group("child").strip())
        if key in found:
            errors.append(f"{where}: {key} listed twice")
        found[key] = (attrs[0], attrs[1], triggers, hidden, strip)
    return found


def main() -> int:
    errors: list[str] = []
    taxonomy = load_taxonomy()
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    links = check_table(data, taxonomy, errors)
    version = data.get("version", "")
    for area in AREAS:
        path = SKILLS / f"{area}.md"
        if not path.exists():
            errors.append(f"skills/{area}.md is missing")
            continue
        text = path.read_text(encoding="utf-8")
        found = parse_skill(area, text, version, errors)
        table = {}
        for link in links:
            if link.get("area") != area:
                continue
            key = (base(link["parent"]), base(link["child"]))
            table[key] = (link["type"], link["min_hit"], set(link["trigger_tokens"]), link["hidden"],
                          link["strip_down"])
        for key, want in table.items():
            got = found.get(key)
            if got is None:
                errors.append(f"skills/{area}.md: link {key[0]} -> {key[1]} is not listed under its parent")
            elif got != want:
                errors.append(f"skills/{area}.md: {key[0]} -> {key[1]} says {got}, the table says {want}")
        for key in found:
            if key not in table:
                errors.append(f"skills/{area}.md: {key[0]} -> {key[1]} is not in the table for area {area!r}")
        print(f"{area:10s} links {len(table):3d}  skill words {len(text.split()):5d}")
    print(f"total links {len(links)}")
    for error in errors:
        print("ERROR", error)
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
