"""Instruction packs and prompt versions for the light models
(plans/2026-11-18-light-detection/P-prompts.md) -- offline: Gemini is a
fake, call_structured is recorded.

Covers:
  - every switch off (the defaults): what the sort, the main read, the
    zone reads and the linked check send -- system prompt, schema, the
    input's text parts, timeout and tries -- is byte for byte master's
    (99aaa85, the hashes below were taken there before any change);
  - each switch puts exactly its pack's text in its place and nothing
    else changes (the prompt with the pack taken back out is master's);
  - the settings' and the benchmark flags' validators;
  - the linked check with thinking "high": its 60 s time box, its tries;
    the sort's 90 s timeout with thinking "high";
  - the zone reads' intact backstop (Z2) and photo_half side (Z5);
  - the linked check's signs (L3) and candidates after the photos (L4).

    python scripts/test_light_packs.py            # the checks
    python scripts/test_light_packs.py --hashes   # print the snapshot hashes

Makes, parts and photos are made up; the photos are coloured squares.
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
_tmp = tempfile.mkdtemp(prefix="light-packs-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "AIzaFAKElightPACKSkey000000000000000001"
os.environ["SECOND_LOOK_ENABLED"] = "false"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
for name in ("GEMINI_MODEL_CHAIN", "GEMINI_TRIAGE_MODEL_CHAIN", "GEMINI_ZONE_CHAIN", "GEMINI_LINK_CHAIN",
             "GEMINI_THINKING_LEVEL_READ", "GEMINI_THINKING_LEVEL_SORT", "GEMINI_THINKING_LEVEL_LINK",
             "GEMINI_IMAGE_RESOLUTION", "GEMINI_READ_ZOOM_HINT", "GEMINI_READ_TOOLS", "INTAKE_SORT_DAMAGE_BOX",
             "INTAKE_READ_MODE", "INTAKE_ZONE_EVIDENCE", "INTAKE_GRID_MARKS", "INTAKE_LINKS", "INTAKE_LINK_CHECK",
             "INTAKE_READ_PACKS", "INTAKE_READ_PROMPT", "INTAKE_READ_ANCHOR", "INTAKE_ZONE_PACKS",
             "INTAKE_ZONE_PHOTO_HALF", "INTAKE_SORT_PROMPT", "INTAKE_LINK_SKILL", "INTAKE_LINK_PROMPT",
             "INTAKE_LINK_SIGNS", "INTAKE_LINK_ORDER"):
    os.environ.pop(name, None)

from PIL import Image  # noqa: E402

import app.ai.structured as structured  # noqa: E402

structured.acquire = lambda key=None: 0.0
structured.OVERLOADED_RETRY_SECONDS = 0.0
structured.CHAIN_BUSY_RETRY_SECONDS = 0.0

from app.ai import extraction, link_check as lc, runs, triage, zone_read  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402

PASSED = [0]
FAILURES: list[tuple[str, str]] = []
_section = {"name": "setup"}


def check(cond, label: str, why="") -> bool:
    if cond:
        PASSED[0] += 1
        print(f"    ok   {label}")
    else:
        FAILURES.append((_section["name"], label))
        print(f"    FAIL {label}" + (f"\n         {why}" if why != "" else ""))
    return bool(cond)


def run_section(name: str, fn) -> None:
    _section["name"] = name
    print(f"\n{name}")
    try:
        fn()
    except Exception as exc:
        FAILURES.append((name, f"crashed: {type(exc).__name__}"))
        print(f"    FAIL crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()


class Settings:
    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


def photo(i: int) -> str:
    path = Path(_tmp, f"photo{i}.jpg")
    if not path.exists():
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (30 * i % 256, 90, (200 - 20 * i) % 256)).save(buf, "JPEG", quality=80)
        path.write_bytes(buf.getvalue())
    return str(path)


# ---------------------------------------------------------------- capture


class Recorder:
    """Stands in for a module's call_structured: keeps each call's kwargs
    and answers `answer` (None: whatever the caller copes with)."""

    def __init__(self, answer=None):
        self.calls: list[dict] = []
        self.tags: list[str | None] = []
        self.answer = answer
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            # The run log's tag on this thread (runs.prompt_tag), kept apart:
            # the kwargs are what is compared with master.
            self.tags.append(runs.current_prompt_tag())
        return self.answer() if callable(self.answer) else self.answer


TAGS: list = []


def recorded(module, fn, answer=None) -> list[dict]:
    rec = Recorder(answer)
    old = module.call_structured
    module.call_structured = rec
    try:
        fn()
    finally:
        module.call_structured = old
    TAGS[:] = rec.tags
    return rec.calls


def _part(part: dict) -> dict:
    """An input part with an image's bytes left out (a grid copy or a
    preview is another file each run): its type, mime type and resolution."""
    if part.get("type") == "image":
        return {"type": "image", "mime_type": part.get("mime_type"), "resolution": part.get("resolution")}
    return part


SENT_KEYS = ("purpose", "system_instruction", "schema", "timeout", "judge_key", "tools_note", "max_tries")


def sent(kwargs: dict) -> dict:
    """What a call sends, as compared: every kwarg but the parser, the
    actor, the chain and the deadline (a clock time), and the input parts
    without image bytes."""
    out = {k: kwargs[k] for k in SENT_KEYS if k in kwargs}
    out["input_parts"] = [_part(p) for p in kwargs.get("input_parts") or []]
    out["kwargs"] = sorted(kwargs)
    return out


def digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


ACTOR = AiActor.cli()


def read_call(**kw) -> dict:
    """draft_report as intake calls it (labels, hints, 120 s)."""
    calls = recorded(extraction, lambda: extraction.draft_report(
        "A grey car was hit at the rear while parked.", [photo(1), photo(2), photo(3)],
        photo_labels=["P03", "P07", "P12"],
        photo_hints=[{"kind": "overview", "area": "rear"}, {"kind": "damage", "area": "rear-left",
                                                             "part": "Rear Bumper", "note": "crack"}, None],
        timeout=120.0, actor=ACTOR, **kw))
    return calls[0]


def bot_read_call() -> dict:
    """draft_report as the Telegram bot calls it (no labels, 45 s)."""
    calls = recorded(extraction, lambda: extraction.draft_report(
        "Scratched my front bumper.", [photo(1)], {"vehicle_info.make": "Honda"}, actor=ACTOR))
    return calls[0]


def sort_call() -> dict:
    calls = recorded(triage, lambda: triage.triage_photos([("P01", photo(1)), ("P02", photo(2))], actor=ACTOR))
    return calls[0]


ZONE_PHOTOS = [("P02", 1, "overview"), ("P05", 2, "damage"), ("P09", 3, "damage")]


def zone_call(zone: str, grid: bool = False, evidence_mode: str = "off") -> dict:
    photos = [(label, photo(i), kind) for label, i, kind in ZONE_PHOTOS]
    calls = recorded(zone_read, lambda: zone_read.read_zone(zone, photos, actor=ACTOR, models=["lite-z"], grid=grid,
                                                            evidence_mode=evidence_mode))
    return calls[0]


def link_group(area: str = "rear", skill: str = "rear") -> lc.Group:
    group = lc.Group(area=area, skill=skill)
    group.photos = [lc.Photo("ph-a", "P02", photo(1), True), lc.Photo("ph-b", "P05", photo(2), True)]
    group.parent_lines = ["- Rear Bumper: hit heavy (Dented, Cracked; Severe; Replace) -- photo P05"]
    group.candidates = [lc.Candidate("C1", "Rear Bumper Beam",
                                     "- C1: Rear Bumper Beam -- behind the Rear Bumper; hidden; confirm at strip-down"),
                        lc.Candidate("C2", "Parking Sensor", "- C2: Parking Sensor -- mounted on the Rear Bumper")]
    return group


def link_call(group: lc.Group | None = None) -> dict:
    """ask() on a group; the recorder answers as parse would (with the signs
    field on: the answers and the signs)."""
    group = group or link_group()
    answer = (lambda: ({}, [])) if lc.skill_choice()[1] else dict
    calls = recorded(lc, lambda: lc.ask(group, ACTOR, runs.Attempts()), answer=answer)
    return calls[0]


def snapshot() -> dict[str, str]:
    """Every request this file compares, at today's settings, by name."""
    out = {"read": digest(sent(read_call())), "read_bot": digest(sent(bot_read_call())),
           "sort": digest(sent(sort_call()))}
    with Settings(intake_sort_damage_box=True):
        out["sort_box"] = digest(sent(sort_call()))
    for zone in zone_read.ZONE_NAMES:
        out[f"zone_{zone}"] = digest(sent(zone_call(zone)))
        out[f"zone_{zone}_grid_reason_box"] = digest(sent(zone_call(zone, True, "reason_box")))
    out["zone_rear_reason"] = digest(sent(zone_call("rear", False, "reason")))
    for area, skill in (("rear", "rear"), ("front", "front"), ("left", "side"), ("underbody", "underbody"),
                        ("wheel", "wheel")):
        out[f"link_{area}"] = digest(sent(link_call(link_group(area, skill))))
    return out


# Taken on master 99aaa85 with this file's snapshot(), before any change.
MASTER = {
    "read": "e533d18189cfd12602c1e3f17c77f14fa949c3085a402b88416ba41fc33de50d",
    "read_bot": "b29f98912472fa594a90e85eb2973624b70abc0ce8aa7593806a9bfd10e01ccf",
    "sort": "282f8c9dba362aee81bb3ece32998b710ef0c09762754f0853cfd028c9c8a1a3",
    "sort_box": "5be119487e0088ce7de6cf0c0ca9f71fb607c9286464737fa268b22ee0183978",
    "zone_front": "dbaa30f8f78fc8e45777e5da676cc29c2972b319db336e95b65f0e1241b48d6e",
    "zone_front_grid_reason_box": "57071a4fd7678bfda153a05bf4abc1ef7edfab5f794803d5d69f617170c6e242",
    "zone_rear": "680c6cb78ce88c4e60a1796fdf5daa6dcbb60036c7678c846383c7a2e4512849",
    "zone_rear_grid_reason_box": "34983269d7119553393a9c14d63e2623ec163f2618b5c7e2916e431c9e176d30",
    "zone_left": "615ae81170a00de88baab3e998bfa5cdc376dcdd800c71d9f531cb73cf9f58eb",
    "zone_left_grid_reason_box": "815569daff68477ef1f55f12c1b8640aaad26f19ae6e2dcb0cabc72a14b20acb",
    "zone_right": "615ae81170a00de88baab3e998bfa5cdc376dcdd800c71d9f531cb73cf9f58eb",
    "zone_right_grid_reason_box": "815569daff68477ef1f55f12c1b8640aaad26f19ae6e2dcb0cabc72a14b20acb",
    "zone_close": "ec97dcb9a87b24d067381073cc85ec50fa96e18379d218b526ea0089f8bbd142",
    "zone_close_grid_reason_box": "befd9d5ec98c344931f9d6b5a4fcd992f1f0d2e1174c845756f720d73d99afbc",
    "zone_rear_reason": "d197b936c1587dba0b61d3b822d00d02d92ba3edba422b16cac4f82b6c6df2c4",
    "link_rear": "057e8cfb086ad707b8e7625b8f7ee9eea0e833c3ac1ae30cf9015c4674bb982e",
    "link_front": "6d629baed06c2f48a91c3c3c7797857d5136a8dd7769355d8da21c82609b2493",
    "link_left": "dd5f0bc1de5af75406ef714e297f9f572cb54d9385d1110807bb77182c00c798",
    "link_underbody": "304e8b05377cddb8abdc344dc0b8ea927da39b138e509d687a1128052731aefd",
    "link_wheel": "3d010174cd23574bbdf97cefe1039bdc65c1c67a4d99c68ff8a9616098ae795c",
}
# plans/2026-11-17-linked-parts/L1-freeze.md: the L1 skill files as git
# keeps them (LF).
L1_FREEZE = {
    "front": "494ce5423b295983010e536c536782395a325496e5c674e33a5d2173b3abc555",
    "rear": "5fefdb7247a84ae72ff228a9a068b760f79b53621d89c8bd3f599c2dfd9e757a",
    "side": "f5c470b4e89370147ad9c62a4e322cb46a5667be037ade0b0ad3f3c3755e172f",
    "underbody": "ac05a8c64ddb9ecb0fa84f52e79e659e217dd7f509159d0bda084a557b5c13ef",
    "wheel": "4cc72a166357017491f54aaf17fcad8bc7fa931181b042ba0ae826d35039606f",
}
LIGHT_OFF = {"intake_read_packs": "", "intake_read_prompt": "v1", "intake_read_anchor": False,
             "intake_zone_packs": "", "intake_zone_photo_half": False, "intake_sort_prompt": "v2",
             "gemini_thinking_level_sort": None, "gemini_thinking_level_link": None, "intake_link_skill": "L1",
             "intake_link_prompt": "v1", "intake_link_signs": False, "intake_link_order": "candidates_first"}


# ---------------------------------------------------------------- the fake Gemini

CREATED: list[dict] = []
ANSWER = {"fn": lambda kwargs: {"answers": []}}


class FakeInteractions:
    def create(self, **kwargs):
        CREATED.append(kwargs)
        out = ANSWER["fn"](kwargs)
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(output_text=out if isinstance(out, str) else json.dumps(out),
                               usage=SimpleNamespace(total_input_tokens=100, total_output_tokens=10))


class Busy(Exception):
    status_code = 503

    def __init__(self):
        super().__init__("Error code: 503 - the model is currently experiencing high demand")


structured.get_client = lambda key=None: SimpleNamespace(interactions=FakeInteractions())


def pk(name: str) -> str:
    from app.ai import prompt_packs

    return prompt_packs.pack(name)


# ---------------------------------------------------------------- 1. off = master


def defaults() -> None:
    from app import config

    s = config.Settings()
    check({k: getattr(s, k) for k in LIGHT_OFF} == LIGHT_OFF, "every light-model setting defaults to today's",
          {k: getattr(s, k) for k in LIGHT_OFF})
    check({k: getattr(settings, k) for k in LIGHT_OFF} == LIGHT_OFF, "and the app runs at them here")
    got = snapshot()
    for name, want in MASTER.items():
        check(got.get(name) == want, f"{name}: prompt, schema, input, timeout and tries byte for byte master's",
              got.get(name))
    check(not any(TAGS), "no run-log tag at the defaults")
    from app.intake import zones

    check(zones.light_options() == {}, "the zone calls get no extra argument at the defaults (read_zone as before)")
    from app.reports.schema import CheckRun

    check("signs" not in CheckRun().model_dump() and "signs" not in CheckRun(status="ok", calls=2).model_dump(),
          "a CheckRun without signs dumps as before")
    # A real call_structured try at the defaults: no "prompt" on the run log.
    attempts = runs.Attempts()
    CREATED.clear()
    ANSWER["fn"] = lambda kwargs: {"description": "x", "category": [], "damage_summary": [], "damaged_parts": []}
    with Settings(gemini_model_chain="fake-reader"), runs.collecting(attempts):
        extraction.draft_report("text", [photo(1)], actor=ACTOR)
    tried = attempts.snapshot()
    check(tried and "prompt" not in tried[0], "the run log's tries have no prompt tag at the defaults", tried)


# ---------------------------------------------------------------- 2. each switch in its place


V1_BULLET_START = "   - LEFT and RIGHT mean the vehicle's own left and right"
V1_BEFORE = "   - Set 'damaged_parts' array with the part names you actually identified.\n"


def _bullet(prompt: str) -> str:
    i = prompt.index(V1_BULLET_START)
    return prompt[i:prompt.index("\n", i) + 1]


def read_switches() -> None:
    master = extraction.SYSTEM_PROMPT
    bullet = _bullet(master)
    off = sent(read_call())
    orient = extraction.system_prompt(["orient"], "v1", False)
    block = pk("orient") + "\n" + "If the reporter stated the side, use it.\n"
    check(orient.count(pk("orient")) == 1 and bullet not in orient and orient.replace(block, bullet) == master,
          "R1 (orient): orient-v1 in place of today's sides rule, the reporter's side kept; nothing else changed")
    for name in ("cues", "small"):
        on = extraction.system_prompt([name], "v1", False)
        check(on.count(pk(name)) == 1 and on.replace(pk(name) + "\n", "") == master
              and on.index(pk(name)) > on.index(bullet) and on.index(pk(name)) < on.index(V1_BEFORE),
              f"R2 ({name}): {name}-v1 after the damage rules, before the damaged_parts rule; nothing else changed")
    both = extraction.system_prompt(["cues", "small"], "v1", False)
    check(both.replace(pk("cues") + "\n" + pk("small") + "\n", "") == master, "R2 (cues,small): cues first, then small")
    allv1 = extraction.system_prompt(["orient", "cues", "small"], "v1", False)
    check(allv1.replace(block, bullet).replace(pk("cues") + "\n" + pk("small") + "\n", "") == master,
          "orient, cues and small together: each in its place")
    with Settings(intake_read_packs="orient"):
        on = sent(read_call())
        tag = TAGS[0]
    check(on["system_instruction"] == orient and on["schema"] == off["schema"] and on["input_parts"] == off["input_parts"]
          and on["timeout"] == off["timeout"], "draft_report with R1: only the system prompt changes (schema, input as before)")
    check(tag and tag.startswith("read-v1+orient-v1@"), "R1: the run log's tag names the version and the pack", tag)

    # R3: v2.
    with Settings(intake_read_prompt="v2", intake_read_packs="orient,cues,small"):
        v2 = sent(read_call())
        bot = sent(bot_read_call())
    tail = extraction.read_tail(["orient", "cues", "small"], "v2")
    check(v2["schema"] == off["schema"], "R3: the drafting schema byte for byte as before")
    check(v2["input_parts"][:-1] == off["input_parts"] and v2["input_parts"][-1] == {"type": "text", "text": tail}
          and tail.startswith("Based on all the photos above:"),
          "R3: the input as before, then one last text part after the photos")
    check(bot["input_parts"][-1] == {"type": "text", "text": tail}, "R3: the bot's drafts (draft_report) get it too")
    text = v2["system_instruction"]
    check(all(text.count(pk(n)) == 1 for n in ("orient", "cues", "small")) and "{PACK" not in text,
          "R3: the three packs in it once each, no placeholder left")
    check(text.index("EVIDENCE RULES") < text.index("THE REPORT FIELDS") < text.index("THE DAMAGE LIST")
          < text.index(pk("orient")) < text.index(pk("cues")) < text.index(pk("small")) < text.index("Part names"),
          "R3: P's order -- evidence rules, the fields, the damage list last with its packs")
    anchors = ("Its first sentence says where the insured car was hit", "the same place as the description's first",
               "and with the description's first sentence")
    check(not any(a in text for a in anchors) and "1. 'description' first, in clear professional loss-adjuster "
          "English. What happened, as reported and as seen." in text, "R3 without R4: none of the anchor pieces")
    with Settings(intake_read_prompt="v2", intake_read_packs="orient,cues,small", intake_read_anchor=True):
        anchored = sent(read_call())["system_instruction"]
        tag = TAGS[0]
    check(all(anchored.count(a) == 1 for a in anchors) and "Then what happened" in anchored,
          "R4: the description's first sentence, point_of_impact and the final check anchored")
    check(tag.startswith("read-v2+orient-v1+cues-v1+small-v1+anchor@"), "R4: tagged", tag)
    bare = extraction.system_prompt([], "v2", False)
    bare_tail = extraction.read_tail([], "v2")
    today = bullet.strip().removeprefix("- ")
    check("{PACK" not in bare and "WHAT COUNTS AS DAMAGE" not in bare and "SMALL PARTS" not in bare
          and "SIDES\n" + today in bare and "C. Write one" in bare and "D. Check before you finish" in bare
          and "E. Check" not in bare and "(step D)" in bare_tail and "small parts" not in bare_tail,
          "v2 with no pack: today's sides rule as the SIDES guide, the steps relettered, the tail to match")
    no_small = extraction.system_prompt(["orient", "cues"], "v2", False)
    check(pk("cues") in no_small and "(WHAT COUNTS AS DAMAGE)" in no_small and "SMALL PARTS" not in no_small,
          "v2 without small: cues and its pointer kept, the small-parts step gone")


def sort_switches() -> None:
    off = sent(sort_call())
    with Settings(intake_sort_prompt="v3"):
        on = sent(sort_call())
        tag = TAGS[0]
        version, boxed = triage.sort_prompt_version(), triage.sort_prompt_version(box=True)
        boxed_prompt = triage.system_prompt(box=True)
    text = on["system_instruction"]
    kinds = "".join(f"    {key}: {desc}\n" for key, desc in triage.PHOTO_KINDS.items())
    check(text.count(pk("orient")) == 1 and kinds in text and triage._SIDES not in text and "{" not in text,
          "S2: orient-v1 in place of today's sides, the same photo kinds, no placeholder left")
    check(text.startswith(triage.SYSTEM_PROMPT.split("\n")[0]) and "When a close-up centres on a small part" in text
          and "a parking sensor pushed in or missing, a bent number plate, a cut or scraped tyre" in text,
          "S2: the small part named, and the extra shows_damage examples")
    check(on["schema"] == off["schema"] and on["input_parts"] == off["input_parts"] and on["timeout"] == off["timeout"],
          "S2: the schema, input and timeout as before")
    check(version == "v3-orient" and boxed == "v3-orient+damage-box" and triage.sort_prompt_version() == "v2-car-sides",
          "S2: its own sort version (the benchmark's sort cache keeps it apart)", (version, boxed))
    check(boxed_prompt.count(triage._BOX_RULE) == 1 and boxed_prompt.replace(triage._BOX_RULE, "") == text,
          "S2 with the damage box: the box rule before the note's line, as on v2")
    check(tag and tag.startswith("sort-v3+orient-v1@"), "S2: tagged", tag)
    # S1: the thinking level's timeout.
    for level, want in ((None, 60.0), ("medium", 60.0), ("high", 90.0), ("gemini-3.5-flash-lite=high", 90.0),
                        ("other-model=high", 60.0)):
        with Settings(gemini_thinking_level_sort=level):
            got = triage.sort_timeout()
            sent_timeout = sort_call()["timeout"]
        check(got == sent_timeout == want, f"S1: thinking {level!r} -> each try {want:g} s", (got, sent_timeout))


def zone_switches() -> None:
    from app.ai import prompt_packs

    closing = zone_read._CLOSING
    for zone in zone_read.ZONE_NAMES:
        off = zone_read.system_prompt(zone)
        rows = prompt_packs.small_rows(zone)
        small = zone_read.system_prompt(zone, packs=["small"])
        check(small.replace(rows + "\n" + closing, closing) == off and rows.startswith("SMALL PARTS ON A DAMAGED PANEL"),
              f"Z1 {zone}: this zone's rows of small-v1 before the closing line, nothing else changed")
        intact = zone_read.system_prompt(zone, packs=["intact"])
        check(intact.replace(pk("intact") + "\n", zone_read._INTACT_LINE) == off and zone_read._INTACT_LINE not in intact,
              f"Z2 {zone}: intact-v1 in place of the checked_intact line")
        oc = zone_read.system_prompt(zone, packs=["orient", "cues"])
        extra = zone_read._ORIENT_NO_SIDE if zone not in zone_read.END_ZONES else ""
        check(oc.replace(pk("orient") + "\n" + extra + pk("cues") + "\n", zone_read._SIDES_LINE) == off,
              f"Z3 {zone}: orient-v1 in place of the sides line" + (" with P's no-side line" if extra else "")
              + ", cues-v1 after it")
        every = zone_read.system_prompt(zone, True, "reason_box", ["small", "intact", "orient", "cues"])
        base = zone_read.system_prompt(zone, True, "reason_box")
        check(every.replace(pk("intact") + "\n", zone_read._INTACT_LINE)
              .replace(pk("orient") + "\n" + extra + pk("cues") + "\n" + rows + "\n", zone_read._SIDES_LINE) == base,
              f"{zone}: all four with grid and reason_box -- intact, orient, cues, small, each in its place")
    check([r.split(":")[0] for r in prompt_packs.small_rows("rear").split("\n")[2:]]
          == ["- Rear Bumper", "- Boot Lid or tailgate", "- Rear Quarter Panel"]
          and len(prompt_packs.small_rows("close").split("\n")) == len(pk("small").split("\n"))
          and len(prompt_packs.small_rows("left").split("\n")) == 6,
          "small-v1's rows per zone: rear's three, a side's four, a close-up zone all")
    labels = ["P02", "P05"]
    off = zone_read.response_schema("rear", labels)
    on = zone_read.response_schema("rear", labels, packs=["intact"])
    desc = on["properties"]["checked_intact"].pop("description")
    check(desc == pk("intact").split("\n", 1)[1] and on == off,
          "Z2: checked_intact's description is intact-v1's rule; the schema otherwise as before")
    from app.intake import zones

    with Settings(intake_zone_packs="small,intact", intake_zone_photo_half=True):
        light = zones.light_options()
    check(light == {"packs": ["small", "intact"], "photo_half": True},
          "zones.light_options: the zone calls' packs and photo_half from the settings", light)
    light = {"packs": light["packs"]}
    for zone in zone_read.ZONE_NAMES:
        call = recorded(zone_read, lambda: zone_read.read_zone(
            zone, [(lb, photo(i), k) for lb, i, k in ZONE_PHOTOS], actor=ACTOR, models=["lite-z"], **light))[0]
        check(call["system_instruction"] == zone_read.system_prompt(zone, packs=["small", "intact"])
              and call["schema"] == zone_read.response_schema(zone, ["P02", "P05", "P09"], packs=["small", "intact"])
              and TAGS[0].startswith(f"zone:{zone}-v1+small-v1+intact-v1@"),
              f"read_zone {zone} with intake_zone_packs small,intact: that prompt, that schema, tagged")
    # Z2's backstop.
    answer = json.dumps({"zone_side_seen": "rear", "damage": [
        {"photo_reference": "P05", "part": "Left Tail Lamp", "ai_confidence": "Low"}],
        "checked_intact": ["Left Tail Lamp", "Rear Bumper"]})
    plain = zone_read.parse_answer("rear", answer, labels)
    backstop = zone_read.parse_answer("rear", answer, labels, packs=["intact"])
    check(plain.intact == ["Left Tail Lamp", "Rear Bumper"] and backstop.intact == ["Rear Bumper"]
          and [i["part"] for i in backstop.items] == ["Left Tail Lamp"],
          "Z2's backstop: a part listed damaged is not kept as intact too (off: as before)", backstop.intact)


def photo_half() -> None:
    labels = ["P02", "P05"]
    for zone in ("left", "right", "close"):
        check(zone_read.system_prompt(zone, photo_half=True) == zone_read.system_prompt(zone)
              and zone_read.response_schema(zone, labels, photo_half=True) == zone_read.response_schema(zone, labels),
              f"Z5: a {zone} zone is not asked (end zones only)")
    for zone in zone_read.END_ZONES:
        on = zone_read.response_schema(zone, labels, True, photo_half=True)
        item = on["properties"]["damage"]["items"]
        check(list(item["properties"])[:3] == ["photo_reference", "photo_half", "grid_cells"]
              and item["required"][:2] == ["photo_reference", "photo_half"]
              and item["properties"]["photo_half"] == {"type": "string", "enum": ["left", "centre", "right"]},
              f"Z5 {zone}: photo_half right after photo_reference, required")
        prompt = zone_read.system_prompt(zone, photo_half=True)
        check(prompt.replace(zone_read.PHOTO_HALF_SENTENCE, "") == zone_read.system_prompt(zone),
              f"Z5 {zone}: one sentence asks for it")
    check([zone_read.side_from_half(e, h) for e, h in (("front", "left"), ("front", "right"), ("rear", "left"),
                                                       ("rear", "right"), ("rear", "centre"), ("left", "left"))]
          == ["Right", "Left", "Left", "Right", None, None], "Z5: the side from the half, as orient-v1's table")

    def parse(zone, seen, items, on=True):
        raw = json.dumps({"zone_side_seen": seen, "damage": [
            {"photo_reference": "P05", "photo_half": half, "part": part, "ai_confidence": "Medium"}
            for part, half in items], "checked_intact": []})
        return [i["part"] for i in zone_read.parse_answer(zone, raw, labels, photo_half=on).items]

    rear = [("Left Tail Lamp", "right"), ("Right Tail Lamp", "right"), ("Left Boot Lid Lamp", "centre"),
            ("Rear Bumper", "left")]
    check(parse("rear", "rear", rear) == ["Tail Lamp (side undetermined)", "Right Tail Lamp", "Left Boot Lid Lamp",
                                          "Rear Bumper"],
          "Z5 rear: a side against its half made undetermined; agreeing, centre or no side kept")
    check(parse("front", "front", [("Left Headlamp", "left"), ("Right Headlamp", "left")])
          == ["Headlamp (side undetermined)", "Right Headlamp"], "Z5 front: the photo's left is the car's right")
    check(parse("rear", "unclear", rear) == [p for p, _ in rear] and parse("rear", "rear", rear, on=False)
          == [p for p, _ in rear], "Z5: photos of an unclear end, or the setting off: the model's sides stand")


def link_switches() -> None:
    from app.ai import prompt_packs

    for name, want in L1_FREEZE.items():
        raw = (lc.SKILLS_DIR / f"{name}.md").read_bytes().replace(b"\r\n", b"\n")
        check(hashlib.sha256(raw).hexdigest() == want, f"skills/{name}.md byte for byte the L1 freeze")
    examples_head, decide = "## Worked examples (in words)\n", "## How to decide"
    no_line = ('A part no photo shows is never "no" for that reason alone: it is "likely", "strip_down" or '
               '"cannot_tell" as the rules above say.')
    signs_l1 = 'Answer each one yes, no or cannot see. A yes is what turns a hidden part into "likely".'
    signs_l3 = ("Answer each one in 'signs' before any candidate: yes, no or cannot_see, with the photo for a yes. "
                'A yes is what turns a hidden part into "likely".')
    for name in L1_FREEZE:
        l1, l2, l3 = lc.skill_text(name), lc.skill_text(name, "L2"), lc.skill_text(name, "L2", True)
        start = l2.index(examples_head)
        back = (l2[:start] + l2[l2.index(decide):]).replace("version: L2", "version: L1", 1).replace("\n" + no_line, "")
        check(l2.startswith(f"skill: {name}  version: L2") and back == l1,
              f"skills-L2/{name}.md: L1 plus the worked examples and the unseen-never-no line, nothing else")
        check(l3.replace(signs_l3, signs_l1) == l2 and signs_l3 in l3, f"skills-L2/{name}.signs.md: only the signs line")
    group = link_group()
    check(lc.system_prompt(group, "v1", "L2", False) == lc.SYSTEM_PROMPT + lc.skill_text("rear", "L2"),
          "L2: today's base prompt with the L2 skill")
    v2 = lc.system_prompt(group, "v2", "L2", False)
    v2s = lc.system_prompt(group, "v2", "L2", True)
    template = prompt_packs.template("link-v2")
    check(v2.startswith(template.split("2. In 'signs'")[0]) and "2. Then answer every candidate" in v2
          and "'signs'" not in v2.split("The surveyor's notes")[0] and v2.endswith(lc.skill_text("rear", "L2")),
          "link v2 without signs: P's base prompt, its step 2 left out, step 3 renumbered")
    check(v2s == template + "\n" + lc.skill_text("rear", "L2", True), "L3 with v2: P's base prompt whole, the .signs skill")
    off_schema = lc.response_schema(["C1"], ["P02"])
    on_schema = lc.response_schema(["C1"], ["P02"], True)
    check(list(on_schema["properties"]) == ["signs", "answers"] and on_schema["required"] == ["signs", "answers"]
          and on_schema["properties"]["answers"] == off_schema["properties"]["answers"]
          and on_schema["properties"]["signs"]["items"]["properties"]["n"]["enum"] == [str(n) for n in range(1, 13)],
          "L3: signs before answers; answers as before")
    got = lc.parse_signs(json.dumps({"signs": [{"n": "4", "answer": "yes", "photo": "p5"},
                                               {"n": "1", "answer": "no", "photo": None},
                                               {"n": "4", "answer": "no", "photo": None},
                                               {"n": "13", "answer": "yes", "photo": None},
                                               {"n": "2", "answer": "maybe", "photo": None},
                                               {"n": "3", "answer": "cannot_see", "photo": "P99"}]}), ["P05"])
    check(got == [{"n": "1", "answer": "no", "photo": None}, {"n": "3", "answer": "cannot_see", "photo": None},
                  {"n": "4", "answer": "yes", "photo": "P05"}],
          "L3: signs by number, once each, bad ones dropped, a photo not sent dropped", got)
    first = lc.build_input(group)
    last = lc.build_input(group, "photos_first")
    lines = ["Candidates to judge, by id (the closed list):"] + [c.line for c in group.candidates]
    check(last[1:-1] == first[1:] and last[-1]["text"] == "\n".join(lines + [lc.CANDIDATES_LAST])
          and last[0]["text"] == first[0]["text"].replace("\n".join(lines) + "\n", ""),
          "L4: the closed list moved after the photos, as the last text part; nothing else changed")
    with Settings(gemini_link_chain="lite-x,lite-y", intake_link_skill="L2", intake_link_prompt="v2",
                  intake_link_signs=True, intake_link_order="photos_first"):
        call = link_call()
        tag = TAGS[0]
    check(call["system_instruction"] == v2s and sent(call)["input_parts"] == [_part(p) for p in last]
          and call["schema"] == lc.response_schema(["C1", "C2"], ["P02", "P05"], True),
          "ask() with L2, v2, signs and photos_first: those prompt, input and schema")
    check(tag.startswith("link-v2+skill-L2+signs+photos_first@"), "linked check tagged", tag)


def link_signs_run() -> None:
    """L3 end to end on the fake client: the signs on the CheckRun only."""
    group = link_group()

    def answer(kwargs):
        return {"signs": [{"n": "1", "answer": "yes", "photo": "P05"}],
                "answers": [{"id": "C1", "cue": "crush", "verdict": "likely", "photo": "P05", "box_2d": None,
                             "reason": "bumper pushed in"}]}

    ANSWER["fn"] = answer
    with Settings(gemini_link_chain="lite-x", intake_link_skill="L2", intake_link_signs=True):
        outcome, results = lc._ask_all("lp-signs", ACTOR, lc.Plan(groups=[group], total=1))
    check(outcome["status"] == "ok" and outcome["signs"] == {"rear": [{"n": "1", "answer": "yes", "photo": "P05"}]}
          and results["Rear Bumper Beam"]["link_check"]["verdict"] == "likely",
          "L3: the signs on the CheckRun by area; the verdicts as without them", outcome)
    from app.reports.schema import CheckRun

    check(CheckRun.model_validate(outcome).model_dump()["signs"] == outcome["signs"], "L3: kept when the CheckRun is saved")
    ANSWER["fn"] = lambda kwargs: {"answers": []}
    with Settings(gemini_link_chain="lite-x"):
        outcome, _ = lc._ask_all("lp-nosigns", ACTOR, lc.Plan(groups=[group], total=1))
    check("signs" not in outcome, "signs off: no signs key on the CheckRun")


def link_thinking() -> None:
    group = link_group()
    with Settings(gemini_link_chain="lite-x,lite-y"):
        check(lc.time_box() == lc.TIME_BOX == 30.0 and link_call()["timeout"] == lc.TIMEOUT,
              "no thinking level: the 30 s box and timeout, as before")
        for level in ("medium", "other=high"):
            with Settings(gemini_thinking_level_link=level):
                check(lc.time_box() == 30.0, f"thinking {level!r}: still 30 s")
        for level in ("high", "lite-y=high"):
            with Settings(gemini_thinking_level_link=level):
                check(lc.time_box() == lc.TIME_BOX_THINKING == 60.0 and link_call()["timeout"] == 60.0,
                      f"L1: thinking {level!r} -> a 60 s box and try timeout")
    # Through call_structured on the fake client, thinking high.
    with Settings(gemini_link_chain="lite-x,lite-y", gemini_thinking_level_link="high"):
        CREATED.clear()
        ANSWER["fn"] = lambda kwargs: {"answers": [{"id": "C1", "cue": "none_seen", "verdict": "no", "photo": None,
                                                    "box_2d": None, "reason": "fine"}]}
        got = lc.ask(group, ACTOR, runs.Attempts())
        check(got["Rear Bumper Beam"]["link_check"]["verdict"] == "no" and len(CREATED) == 1
              and CREATED[0]["generation_config"] == {"thinking_level": "high"} and 55 <= CREATED[0]["timeout"] <= 60,
              "L1: the request asks for high thinking; its timeout is the 60 s box",
              [(c.get("generation_config"), round(c.get("timeout", 0))) for c in CREATED])
        CREATED.clear()
        ANSWER["fn"] = lambda kwargs: Busy()
        try:
            lc.ask(group, ACTOR, runs.Attempts())
            check(False, "busy throughout should fail the call")
        except Exception:
            check(len(CREATED) == lc.MAX_TRIES == 2, "L1: busy throughout -- still two tries in all", len(CREATED))
        old = lc.TIME_BOX_THINKING
        lc.TIME_BOX_THINKING = 0.3
        ANSWER["fn"] = lambda kwargs: (time.sleep(1.5), {"answers": []})[1]
        started = time.monotonic()
        try:
            lc.ask(group, ACTOR, runs.Attempts())
            check(False, "a hung call should be given up")
        except TimeoutError:
            check(time.monotonic() - started < 1.2, "L1: the (thinking) box gives up on the wall clock",
                  round(time.monotonic() - started, 2))
        finally:
            lc.TIME_BOX_THINKING = old
        time.sleep(1.5)
    ANSWER["fn"] = lambda kwargs: {"answers": []}


def run_log_tag() -> None:
    attempts = runs.Attempts()
    ANSWER["fn"] = lambda kwargs: {"description": "x", "category": [], "damage_summary": [], "damaged_parts": []}
    with Settings(gemini_model_chain="fake-reader", intake_read_packs="orient"), runs.collecting(attempts):
        extraction.draft_report("text", [photo(1)], actor=ACTOR)
    tried = attempts.snapshot()
    from app.ai import prompt_packs

    want = prompt_packs.tag("read", "v1", ["orient"], extraction.system_prompt(["orient"], "v1", False))
    check(tried and tried[0].get("prompt") == want and len(want.split("@")[1]) == 12,
          "a try made with a switch on carries the prompt tag (version, packs, sha) in the run log", tried)
    check(runs.current_prompt_tag() is None, "and the tag ends with the call")


# ---------------------------------------------------------------- 3. validators, flags, compose


def validators() -> None:
    from app import config

    s = config.Settings(intake_read_packs=" small, ORIENT ", intake_zone_packs="intact,small,cues,orient",
                        intake_read_prompt="V2", intake_link_skill="l2", intake_link_signs=True,
                        gemini_thinking_level_link="", gemini_thinking_level_sort=" HIGH ")
    check((s.intake_read_packs, s.intake_zone_packs, s.intake_read_prompt, s.intake_link_skill,
           s.gemini_thinking_level_link, s.gemini_thinking_level_sort)
          == ("orient,small", "orient,cues,small,intact", "v2", "L2", None, "high"),
          "settings written the one way (packs in their order, blank thinking = none)")
    for bad in ({"intake_read_packs": "orient,intact"}, {"intake_zone_packs": "everything"},
                {"intake_read_prompt": "v3"}, {"intake_sort_prompt": "v1"}, {"intake_link_skill": "L3"},
                {"intake_link_prompt": "v0"}, {"intake_link_order": "last"}, {"intake_read_anchor": True},
                {"intake_link_signs": True}, {"gemini_thinking_level_link": "extreme"}):
        try:
            config.Settings(**bad)
            check(False, f"{bad} should be refused")
        except ValueError:
            check(True, f"refused at start-up: {bad}")

    import benchmark_intake as bi

    defaults = vars(bi.parse_args([]))
    check({k: defaults[k] for k in bi.LIGHT_ARGS} == bi.LIGHT_ARGS, "the light flags' defaults are LIGHT_ARGS (left out of args)")
    got = bi.model_settings(bi.parse_args(
        ["--read-packs", "small,orient", "--read-prompt", "v2", "--read-anchor", "--sort-prompt", "v3",
         "--read-mode", "zones", "--zone-packs", "intact", "--zone-photo-half", "--links", "shadow",
         "--link-check", "lite", "--thinking-level-link", "high", "--link-skill", "L2", "--link-prompt", "v2",
         "--link-signs", "--link-order", "photos_first", "--thinking-level-sort", "high"]))
    want = {"intake_read_packs": "orient,small", "intake_read_prompt": "v2", "intake_read_anchor": True,
            "intake_sort_prompt": "v3", "intake_zone_packs": "intact", "intake_zone_photo_half": True,
            "gemini_thinking_level_link": "high", "intake_link_skill": "L2", "intake_link_prompt": "v2",
            "intake_link_signs": True, "intake_link_order": "photos_first", "gemini_thinking_level_sort": "high"}
    check({k: got.get(k) for k in want} == want, "every flag sets its setting", got)
    for bad, says in ((["--read-packs", "everything"], "--read-packs"), (["--read-prompt", "v9"], "--read-prompt"),
                      (["--read-anchor"], "--read-anchor"), (["--zone-packs", "small"], "--read-mode zones"),
                      (["--zone-photo-half"], "--read-mode zones"), (["--sort-prompt", "v1"], "--sort-prompt"),
                      (["--thinking-level-link", "high"], "--link-check lite"),
                      (["--link-skill", "L2"], "--link-check lite"),
                      (["--links", "on", "--link-check", "lite", "--link-skill", "L9"], "--link-skill"),
                      (["--links", "on", "--link-check", "lite", "--link-signs"], "--link-skill L2"),
                      (["--links", "on", "--link-check", "lite", "--link-order", "random"], "--link-order"),
                      (["--read-mode", "zones", "--zone-packs", "small,bogus"], "--zone-packs")):
        try:
            bi.model_settings(bi.parse_args(bad))
            check(False, f"{bad} should stop the run")
        except SystemExit as exc:
            check(says in str(exc.code), f"stops the run: {' '.join(bad)}", exc.code)
    plain = bi.ai_settings(settings)
    check(not set(bi.LIGHT_DEFAULTS) & set(plain) and "packs_sha256" not in plain,
          "ai_settings at the defaults: as before (no light key)")
    with Settings(intake_read_packs="orient", intake_sort_prompt="v3", intake_zone_packs="small"):
        shown = bi.ai_settings(settings)
    check(shown.get("intake_read_packs") == "orient" and shown.get("intake_sort_prompt") == "v3"
          and "intake_zone_packs" not in shown and len(shown["packs_sha256"]) == 8,
          "ai_settings records the switches that act (zone packs only in zone mode) and the packs' sha256",
          sorted(shown))
    with Settings(intake_read_mode="zones", intake_zone_packs="small", intake_links="on", intake_link_check="lite",
                  intake_link_skill="L2"):
        shown = bi.ai_settings(settings)
    check(shown.get("intake_zone_packs") == "small" and shown.get("intake_link_skill") == "L2"
          and len(shown.get("link_skills_l2_sha256") or {}) == 10, "with zones and the linked check: theirs too")


def compose() -> None:
    from app import config

    body = (ROOT.parent.parent / "infra" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    shared, rest = body.split("\nservices:", 1)
    lines = {
        "INTAKE_READ_PACKS": "", "INTAKE_READ_PROMPT": "v1", "INTAKE_READ_ANCHOR": "false", "INTAKE_ZONE_PACKS": "",
        "INTAKE_ZONE_PHOTO_HALF": "false", "INTAKE_SORT_PROMPT": "v2", "GEMINI_THINKING_LEVEL_SORT": "",
        "GEMINI_THINKING_LEVEL_LINK": "", "INTAKE_LINK_SKILL": "L1", "INTAKE_LINK_PROMPT": "v1",
        "INTAKE_LINK_SIGNS": "false", "INTAKE_LINK_ORDER": "candidates_first",
    }
    for name, default in lines.items():
        want = f"\n    - {name}=${{{name}:-{default}}}\n"
        check(shared.count(want) == 1 and name not in rest, f"prod compose passes {name} (default {default!r}) "
              "in the shared block both containers take")
    s = config.Settings(**{name.lower(): value for name, value in lines.items()})
    check({k: getattr(s, k) for k in LIGHT_OFF} == LIGHT_OFF, "compose's defaults are config's: today's behaviour")
    try:
        import yaml

        services = (yaml.safe_load(body) or {}).get("services") or {}
        for svc in ("api", "telegram-bot"):
            env = [str(e) for e in (services.get(svc) or {}).get("environment") or []]
            check(all(any(e.startswith(f"{n}=") for e in env) for n in lines), f"{svc} gets all of them")
    except ImportError:
        pass


def main() -> int:
    if "--hashes" in sys.argv:
        for name, value in snapshot().items():
            print(f'    "{name}": "{value}",')
        return 0
    from app.reports.db import init_db

    init_db()
    for name, fn in (
        ("1. every switch off: master's requests", defaults),
        ("2a. main read: R1, R2, R3, R4", read_switches),
        ("2b. sort: S1, S2", sort_switches),
        ("2c. zone reads: Z1, Z2, Z3", zone_switches),
        ("2d. zone reads: Z5 photo_half", photo_half),
        ("2e. linked check: L2, L3, L4", link_switches),
        ("2f. linked check: L3 signs on the CheckRun", link_signs_run),
        ("2g. linked check: L1 thinking high, its box and tries", link_thinking),
        ("2h. the run log's prompt tag", run_log_tag),
        ("3. validators and benchmark flags", validators),
        ("3b. prod compose", compose),
    ):
        run_section(name, fn)
    print(f"\ntest_light_packs: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
