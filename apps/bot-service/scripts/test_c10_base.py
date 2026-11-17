"""Contract 10's base (plans/2026-11-17-linked-parts/contract10.md, Step 0)
-- offline, no Gemini (a fake client that only records).

Covers:
  - 4.6: the Contract 10 settings, their defaults (everything off) and
    their parsers -- each written the one way, a typo stopping the app;
  - 4.1: the linked-part fields -- LinkCheck, SecondLookItem (source
    "link"), DamageSummaryItem (out of the drafting schema, which stays
    byte for byte as it was), Evidence by "link" / "link_check",
    SecondLook.link_run; old data loading; a field at its default left
    out of a dump, so the second look and the damage list save byte for
    byte what feat/c9-integration saved (golden hashes taken there);
  - the stubs: intake/links.py (the table's loading; build / for_parent /
    hit_of, filled by agent R1, on an empty table) and ai/link_check.py
    (no call, no write while off);
  - the dashboard's linked-parts fixture loading through the draft model.

    python scripts/test_c10_base.py

Makes, parts and plates are made up.
"""
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="c10-base-test-")
os.environ["STORAGE_DIR"] = _tmp
os.environ["SECRETS_DIR"] = str(Path(_tmp, "secrets"))
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["GEMINI_API_KEY"] = "fake-c10-company-key"
os.environ["RUN_BACKGROUND_CLEANUP"] = "false"
os.environ["MIN_FREE_DISK_GB"] = "0"
C10_SETTINGS = ("intake_links", "intake_links_max", "intake_links_types", "intake_links_side",
                "intake_links_unknown_hit", "intake_links_intact", "intake_links_catalogue", "intake_link_check",
                "gemini_link_chain", "intake_link_check_max_calls", "gemini_thinking_level_link",
                "telegram_full_detection")
for name in C10_SETTINGS:
    os.environ.pop(name.upper(), None)

import app.ai.structured as structured  # noqa: E402

# Every client a Gemini call asked for: none, here.
CLIENTS: list = []


def _recording_client(key=None):
    CLIENTS.append(key)
    raise AssertionError("no Gemini call in contract 10's base")


structured.get_client = _recording_client
structured.acquire = lambda key=None: 0.0

from app import config  # noqa: E402
from app.ai import link_check  # noqa: E402
from app.ai.extraction import _response_schema  # noqa: E402
from app.ai.keys import AiActor  # noqa: E402
from app.config import settings  # noqa: E402
from app.intake import links  # noqa: E402
from app.parts.prefill import item_key  # noqa: E402
from app.reports.db import SessionLocal, init_db  # noqa: E402
from app.reports.models import IntakeSession, Report  # noqa: E402
from app.reports.schema import (  # noqa: E402
    DamageSummaryItem,
    Evidence,
    LinkCheck,
    SecondLook,
    SecondLookItem,
    SecurityIncidentDraft,
)
from app.reports.taxonomy import CANONICAL_PARTS, UNDETERMINED_SUFFIX  # noqa: E402

STRONG = "gemini-3-flash-preview"
LITE_A, LITE_B = "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"
# The drafting schema and the whole draft model's (test_c9_base's goldens):
# the new fields are out of both.
GOLDEN_DRAFT_SCHEMA = "7ccb5ce425dc1fe68971672f41992753e1431b535edf941c45aa648e3d6d18d0"
GOLDEN_MODEL_SCHEMA = "b94a425945ec99d7a04605f0f48f57d920662de5ecb49491b7b38eb60dc8c1a7"
# Taken on feat/c9-integration (2f6918b) before contract 10's fields, from
# the contract 9 fixture (apps/dashboard/scripts/fixtures/evidence-sample.
# json): json.dumps of its second look and its damage items dumped through
# the models, the same through the whole draft model, an empty second
# look, and the second look's model_dump_json.
GOLDEN_LOOK = "ca85c5d41374c291752c8c2c948ce323587606a891dd34dd4aa0981077773b0d"
GOLDEN_ITEMS = "41416af548f2c83977cc552cc64c101727129c4e85aa308983b8ec634e39c36e"
GOLDEN_DRAFT_PARTS = "2a0e1244671b253b7a6fff4512653fb097fa34300efa6898004a8480b5aa22fa"
GOLDEN_EMPTY_LOOK = "507a6a075218ea4f712e808b44ec068c361e9fa4de4210b04b9f924a96342253"
GOLDEN_LOOK_JSON = "3970e450eadabd48841accf00657c4f9c414b46cf927923836de843825d48603"
C10_ITEM_FIELDS = ("linked_from", "link_type", "hidden", "needs_strip_down")
C10_SUGGESTION_FIELDS = C10_ITEM_FIELDS + ("link_rule", "hit", "bucket", "side_from", "intact_in", "link_check", "oem")
DASHBOARD = ROOT.parent / "dashboard"

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


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj).encode()).hexdigest()


def refuses(fn) -> bool:
    try:
        fn()
    except ValueError:  # pydantic's ValidationError is one too
        return True
    return False


class Settings:
    """Sets settings for a block and puts them back."""

    def __init__(self, **values):
        self.values = values

    def __enter__(self):
        self.old = {k: getattr(settings, k) for k in self.values}
        for k, v in self.values.items():
            setattr(settings, k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            setattr(settings, k, v)


# ---------------------------------------------------------------- 4.6 settings


def settings_block() -> None:
    s = config.Settings()
    want = {"intake_links": "off", "intake_links_max": 12, "intake_links_types": "attached,opens_with,behind,crush",
            "intake_links_side": "firm", "intake_links_unknown_hit": "medium", "intake_links_intact": "demote",
            "intake_links_catalogue": False, "intake_link_check": "off",
            "gemini_link_chain": f"{LITE_A},{LITE_B}", "intake_link_check_max_calls": 2,
            "gemini_thinking_level_link": None, "telegram_full_detection": False}
    got = {k: getattr(s, k) for k in want}
    check(got == want, "section 4.6's settings and defaults: everything off", {k: v for k, v in got.items() if want[k] != v})
    check(set(want) == set(C10_SETTINGS), "the twelve of them")
    check(STRONG not in s.gemini_link_chain, "the linked check's chain holds no strong model")
    check(s.second_look_enabled is False and s.intake_check_mode == "off" and s.intake_read_mode == "single",
          "contract 9's switches as they were")

    written = config.Settings(intake_links=" Shadow ", intake_links_max="4", intake_links_types="crush, BEHIND,crush",
                              intake_links_side="STRICT", intake_links_unknown_hit="", intake_links_intact=" drop",
                              intake_links_catalogue="true", intake_link_check="LITE", gemini_link_chain=" a ,b,a ",
                              intake_link_check_max_calls=0, gemini_thinking_level_link=" LOW ",
                              telegram_full_detection="1")
    check((written.intake_links, written.intake_links_max, written.intake_links_types, written.intake_links_side,
           written.intake_links_unknown_hit, written.intake_links_intact, written.intake_links_catalogue,
           written.intake_link_check, written.gemini_link_chain, written.intake_link_check_max_calls,
           written.gemini_thinking_level_link, written.telegram_full_detection)
          == ("shadow", 4, "behind,crush", "strict", "medium", "drop", True, "lite", "a,b", 0, "low", True),
          "each written the one way (types in table order, once); blank is the default")
    blank = config.Settings(intake_links="", intake_links_side=" ", intake_links_intact="", intake_link_check="")
    check((blank.intake_links, blank.intake_links_side, blank.intake_links_intact, blank.intake_link_check)
          == ("off", "firm", "demote", "off"), "blank: off / firm / demote / off")
    for field, bad in (("intake_links", "yes"), ("intake_links_max", -1), ("intake_links_types", ""),
                       ("intake_links_types", "attached,glued"), ("intake_links_side", "loose"),
                       ("intake_links_unknown_hit", "severe"), ("intake_links_intact", "hide"),
                       ("intake_link_check", "decision"), ("gemini_link_chain", " , "),
                       ("intake_link_check_max_calls", -2), ("gemini_thinking_level_link", "extreme")):
        check(refuses(lambda: config.Settings(**{field: bad})), f"{field}={bad!r} stops the app at start-up")

    os.environ["INTAKE_LINKS"], os.environ["TELEGRAM_FULL_DETECTION"] = "on", "true"
    try:
        env = config.Settings()
        check(env.intake_links == "on" and env.telegram_full_detection is True, "read from the environment")
    finally:
        os.environ.pop("INTAKE_LINKS"), os.environ.pop("TELEGRAM_FULL_DETECTION")

    # The parsers on their own (the benchmark checks its flags with them).
    check(config.LINK_TYPES == ("attached", "opens_with", "behind", "crush")
          and config.HIT_LEVELS == ("light", "medium", "heavy", "crush"), "LINK_TYPES and HIT_LEVELS, in rank order")
    check(config.link_types(" opens_with,ATTACHED ") == ["attached", "opens_with"] and config.link_types("") == [],
          "link_types: the types in table order; blank is none")
    check(refuses(lambda: config.link_types("attached,welded")), "link_types: an unknown type is refused")
    check(config.normal_link_types("attached,opens_with,behind,crush") == settings.intake_links_types,
          "the default types written the one way are the default")
    check((config.normal_links_mode(None), config.normal_links_side(""), config.normal_hit_level(None),
           config.normal_hit_level(" Heavy "), config.normal_links_intact(None), config.normal_link_check(None))
          == ("off", "firm", "medium", "heavy", "demote", "off"), "the choice parsers and their blanks")
    check(config.normal_count(" 3 ", "n") == 3 and config.normal_count(0) == 0, "normal_count: whole numbers, 0 or more")
    check(refuses(lambda: config.normal_count("2.5", "n")) and refuses(lambda: config.normal_count(-1, "n"))
          and refuses(lambda: config.normal_count("", "n")), "normal_count: a fraction, a negative, blank refused")
    check(refuses(lambda: config.normal_hit_level("severe")) and refuses(lambda: config.normal_link_check("on")),
          "a hit level or check mode that isn't one is refused")


# ---------------------------------------------------------------- 4.1 schema


def schema_fields() -> None:
    check(sha(_response_schema()) == GOLDEN_DRAFT_SCHEMA, "the drafting schema is byte for byte as before (golden)",
          sha(_response_schema()))
    check(sha(SecurityIncidentDraft.model_json_schema()) == GOLDEN_MODEL_SCHEMA,
          "the draft model's whole JSON schema too: nothing new in it")
    props = DamageSummaryItem.model_json_schema()["properties"]
    check(not set(C10_ITEM_FIELDS) & set(props), "linked_from / link_type / hidden / needs_strip_down: not in the "
          "damage item's schema (SkipJsonSchema)")

    full = {"key": "Rear Bumper Beam", "part": "Rear Bumper Beam", "source": "link", "photo_reference": "@p3",
            "evidence": [{"photo": "@p3", "by": "link"},
                         {"photo": "@p4", "box": [10, 20, 300, 400], "box_kind": "ai", "by": "link_check",
                          "reason": "beam bent", "img_w": 1600, "img_h": 1200}],
            "linked_from": ["Rear Bumper"], "link_type": "behind", "link_rule": "L1:rear-bumper>beam",
            "hit": "heavy", "bucket": "strip_down", "hidden": True, "needs_strip_down": True, "side_from": "Left X",
            "intact_in": ["@p9"], "link_check": {"verdict": "strip_down", "cue": "cannot_see",
                                                 "model": LITE_A, "reason": "behind the cover"},
            "oem": ["REAR BUMPER"]}
    item = SecondLookItem.model_validate(full)
    dumped = item.model_dump()
    check(all(dumped[k] == full[k] for k in C10_SUGGESTION_FIELDS if k != "link_check")
          and dumped["link_check"] == full["link_check"] and dumped["source"] == "link",
          "a link suggestion keeps every field through a dump", dumped)
    check(SecondLookItem.model_validate(dumped).model_dump() == dumped
          and json.loads(item.model_dump_json()) == dumped, "and round-trips (model_dump and model_dump_json)")
    check([e["by"] for e in dumped["evidence"]] == ["link", "link_check"]
          and dumped["evidence"][0]["box"] is None and dumped["evidence"][1]["box"] == [10, 20, 300, 400],
          "Evidence.by takes \"link\" (no box) and \"link_check\" (a box)")
    check(Evidence(photo="@p1", by="link").by == "link", "Evidence by \"link\"")
    check(LinkCheck(verdict="no").model_dump() == {"verdict": "no", "cue": None, "model": None, "reason": None},
          "LinkCheck: a verdict, the rest empty")
    check(isinstance(item.link_check, LinkCheck), "link_check is a LinkCheck")

    old = SecondLookItem.model_validate({"part": "Boot Lid Lock", "source": "zone"})
    check((old.linked_from, old.link_type, old.link_rule, old.hit, old.bucket, old.hidden, old.needs_strip_down,
           old.side_from, old.intact_in, old.link_check, old.oem)
          == ([], None, None, None, None, False, False, None, [], None, []), "an old suggestion loads, empty")
    check(not set(C10_SUGGESTION_FIELDS) & set(old.model_dump()) and not set(C10_SUGGESTION_FIELDS) & set(
        old.model_dump(mode="json")) and not set(C10_SUGGESTION_FIELDS) & set(json.loads(old.model_dump_json())),
        "and dumps without them (python, json mode, json)")
    some = SecondLookItem(part="Rear Number Plate", source="link", linked_from=["Rear Bumper"], hidden=False,
                          bucket="likely").model_dump()
    check([k for k in C10_SUGGESTION_FIELDS if k in some] == ["linked_from", "bucket"],
          "only the fields off their defaults are dumped (hidden=False given: left out)", some)

    look = SecondLook.model_validate({"items": [full], "link_run": {"status": "ok", "by": "lite", "model": LITE_A,
                                                                    "seconds": 2.0, "calls": 2}})
    out = look.model_dump()
    check(out["link_run"] == {"status": "ok", "by": "lite", "model": LITE_A, "note": None, "seconds": 2.0, "calls": 2}
          and out["items"][0]["link_check"]["verdict"] == "strip_down", "SecondLook.link_run (a CheckRun), items kept")
    check("link_run" not in SecondLook.model_validate({"status": "ok"}).model_dump(), "no link_run: not dumped")
    check(sha(SecondLook().model_dump()) == GOLDEN_EMPTY_LOOK, "an empty second look dumps byte for byte as before")

    added = {"part": "Boot Lid Hinge", "photo_reference": "@p5", "human_verified": True, "added_from": "suggestion",
             "linked_from": ["Boot Lid"], "link_type": "opens_with", "hidden": True, "needs_strip_down": True}
    draft = SecurityIncidentDraft.model_validate({"description": "x", "damage_summary": [added, {"part": "Boot Lid"}],
                                                  "second_look": {"items": [full]}})
    got = draft.model_dump()
    check({k: got["damage_summary"][0][k] for k in C10_ITEM_FIELDS} == {k: added[k] for k in C10_ITEM_FIELDS},
          "the wizard's autosave (through the draft model) keeps an added linked part's fields")
    check(not set(C10_ITEM_FIELDS) & set(got["damage_summary"][1]), "and leaves them off an item without them")
    check(got["second_look"]["items"][0]["link_rule"] == "L1:rear-bumper>beam", "and the second look's link items")
    old_item = DamageSummaryItem.model_validate({"part": "Rear Bumper", "photo_reference": "P03"})
    check((old_item.linked_from, old_item.link_type, old_item.hidden, old_item.needs_strip_down)
          == ([], None, False, False), "a damage item saved before them loads, empty")


def byte_for_byte() -> None:
    path = DASHBOARD / "scripts" / "fixtures" / "evidence-sample.json"
    if not path.exists():
        check(True, "the dashboard isn't beside this checkout: skipped")
        return
    data = json.loads(path.read_text(encoding="utf-8"))["data"]
    look = SecondLook.model_validate(data["second_look"])
    check(sha(look.model_dump()) == GOLDEN_LOOK, "contract 9's second look dumps byte for byte as on feat/c9-integration")
    check(hashlib.sha256(look.model_dump_json().encode()).hexdigest() == GOLDEN_LOOK_JSON, "...and as JSON")
    items = [DamageSummaryItem.model_validate(it).model_dump() for it in data["damage_summary"]]
    check(sha(items) == GOLDEN_ITEMS, "its damage items too")
    draft = SecurityIncidentDraft.model_validate(data).model_dump()
    check(sha([draft["second_look"], draft["damage_summary"]]) == GOLDEN_DRAFT_PARTS,
          "and both through the whole draft model (the wizard's autosave)")


# ---------------------------------------------------------------- stubs


def links_stub() -> None:
    check(links.TABLE_PATH == ROOT / "app" / "intake" / "link_table.json", "TABLE_PATH: app/intake/link_table.json")
    if links.TABLE_PATH.exists():
        check(links.TABLE is not None and links.TABLE_VERSION == links.TABLE["version"],
              "the table on disk loaded at import, with its version")
    else:
        check(links.TABLE is None and links.TABLE_VERSION is None and links.table_sha() is None,
              "no table on disk (agent L adds it): TABLE, TABLE_VERSION and table_sha are None")
    folder = Path(_tmp, "tables")
    folder.mkdir()
    check(links.load_table(folder / "missing.json") is None, "a missing table: None")
    for name, body in (("broken.json", "{not json"), ("list.json", "[]"), ("noversion.json", '{"links": []}'),
                       ("blank.json", '{"version": " ", "links": []}'), ("nolinks.json", '{"version": "L1"}'),
                       ("linksdict.json", '{"version": "L1", "links": {}}')):
        (folder / name).write_text(body, encoding="utf-8")
        check(links.load_table(folder / name) is None, f"{name}: not a table, None (links off)")
    good = folder / "good.json"
    good.write_text('{"version": "L1", "built_from": [], "links": []}', encoding="utf-8")
    check(links.load_table(good) == {"version": "L1", "built_from": [], "links": []}, "a table: loaded")
    check(links.load_table(str(good)) is not None, "a path as a string too")
    check(links.table_sha(good) == hashlib.sha256(good.read_bytes()).hexdigest()
          and links.table_sha(folder / "missing.json") is None, "table_sha: the file's sha256; None with no file")

    opts = links.options()
    check(opts == links.Options() and opts.types == config.LINK_TYPES and opts.mode == "off" and opts.max_items == 12,
          "options(): the settings' defaults", opts)
    custom = SimpleNamespace(intake_links="shadow", intake_links_max=5, intake_links_types="behind,attached",
                             intake_links_side="strict", intake_links_unknown_hit="heavy",
                             intake_links_intact="drop", intake_links_catalogue=True)
    check(links.options(custom) == links.Options("shadow", 5, ("attached", "behind"), "strict", "heavy", "drop", True),
          "options(source): from anything with the settings' names (the benchmark's replay)")
    parent = {"part": "Rear Bumper", "condition_tokens": ["Crumpled"], "severity": "Severe", "remarks": "Replace"}
    table = links.load_table(good)
    # Agent R1 filled the stubs (scripts/test_links.py covers them): an
    # empty table or none links nothing; the words say how hard.
    check(links.build([parent], [], [], {}, table, opts) == [] and links.build([parent], [], [], {}, None, opts) == [],
          "build: nothing from an empty table or none")
    check(links.for_parent(parent, {"damage_summary": [parent]}, [], table, opts) == []
          and links.for_parent(parent, {"damage_summary": [parent]}, [], None, opts) == [],
          "for_parent: nothing from an empty table or none")
    check(links.hit_of(parent) == "crush" and links.hit_of({"part": "Rear Bumper"}) is None,
          "hit_of: Crumpled is the crush path; no words, None")
    check(links.BUCKETS == ("seen", "likely", "strip_down") and links.LINK_SIDES == ("same", "impact", "none")
          and links.HIT_LEVELS == config.HIT_LEVELS, "the names: buckets, sides, hit levels")


def link_check_stub() -> None:
    rid = "c10-link-check"
    db = SessionLocal()
    try:
        db.add(Report(id=rid, status="intake", channel="dashboard", reporter_chat_id="test",
                      data={"description": "", "damage_summary": [{"part": "Rear Bumper"}],
                            "second_look": SecondLook(items=[SecondLookItem(part="Rear Bumper Beam", source="link",
                                                                            linked_from=["Rear Bumper"])]).model_dump()}))
        db.add(IntakeSession(report_id=rid, stage="review", reporter_notes="", ai_draft={"damage_summary": []}))
        db.commit()
    finally:
        db.close()

    def saved() -> dict:
        db = SessionLocal()
        try:
            return {"data": copy.deepcopy(db.get(Report, rid).data),
                    "ai_draft": copy.deepcopy(db.get(IntakeSession, rid).ai_draft)}
        finally:
            db.close()

    before = saved()
    check(settings.intake_link_check == "off", "intake_link_check: \"off\" by default")
    CLIENTS.clear()
    for mode in ("off", "lite"):
        with Settings(intake_link_check=mode):
            check(link_check.run_for_intake(rid, AiActor.cli()) is None and not CLIENTS and saved() == before,
                  f"mode {mode}: no call, nothing written (Step 0's stub)")
    check(link_check.VERDICTS == ("seen", "likely", "strip_down", "no", "cannot_tell") and "none_seen" in link_check.CUES,
          "the verdicts and cues of schema.LinkCheck")


# ---------------------------------------------------------------- fixture


def fixture() -> None:
    path = DASHBOARD / "scripts" / "fixtures" / "links-sample.json"
    if not path.exists():
        check(True, "the dashboard isn't beside this checkout: skipped")
        return
    text = path.read_text(encoding="utf-8")
    doc = json.loads(text)
    data = doc["data"]
    draft = SecurityIncidentDraft.model_validate(data)
    look = draft.second_look
    check(SecondLook.model_validate(data["second_look"]).model_dump() == data["second_look"]
          and [DamageSummaryItem.model_validate(it).model_dump() for it in data["damage_summary"]]
          == data["damage_summary"], "the file is exactly what the server dumps (defaults left out)")
    refs = {p["ref"] for p in doc["photos"]}
    check(len(doc["photos"]) == 10 and all(re.fullmatch(r"@ph\d\d", r) for r in refs)
          and any(p["h"] > p["w"] for p in doc["photos"]), "10 photos as \"@phNN\", one portrait")
    used = [it.photo_reference for it in list(draft.damage_summary) + list(look.items)]
    used += [e.photo for it in list(draft.damage_summary) + list(look.items) for e in it.evidence]
    used += [r for it in look.items for r in it.seen_in + it.intact_in]
    check(set(used) <= refs, "every photo it names is one of them")

    listed = {it.part for it in draft.damage_summary}
    linked = [it for it in look.items if it.source == "link"]
    open_ = [it for it in linked if it.key not in look.dismissed]
    check(len(linked) == 6 and len(open_) == 5 and any(it.source == "zone" for it in look.items),
          "6 linked parts (one dismissed) beside a zone read's suggestion")
    check({it.bucket for it in open_} == {"seen", "likely", "strip_down"}, "all three buckets")
    check({p for it in linked for p in it.linked_from} == {"Rear Bumper", "Left Rear Door"} <= listed,
          "two listed parents")
    hidden = [it for it in linked if it.hidden]
    check(len(hidden) == 1 and hidden[0].bucket == "strip_down" and hidden[0].needs_strip_down,
          "one hidden part, to confirm at strip-down")
    checked = [it for it in linked if it.link_check]
    check(len(checked) == 1 and checked[0].link_check.verdict == "seen"
          and any(e.by == "link_check" and e.box for e in checked[0].evidence), "one with the linked check's answer and box")
    undetermined = [it for it in linked if it.part.endswith(UNDETERMINED_SUFFIX)]
    check(len(undetermined) == 1 and undetermined[0].side_from is None, "one \"(side undetermined)\" child, no side taken")
    sided = [it for it in linked if it.side_from]
    check(sided and all(it.side_from in listed and it.part.split()[0] == it.side_from.split()[0] for it in sided),
          "\"same side as\": side_from a listed part, on its side")
    check(any(it.link_type == "crush" for it in open_) and any(it.intact_in for it in open_)
          and any(it.oem for it in open_), "a crush link, one a zone read saw intact, catalogue diagram names")
    check(not any(set(it.covers) & listed for it in open_), "nothing pre-ticked: no open linked part is on the list")
    added = [it for it in draft.damage_summary if it.added_from == "suggestion"]
    check(len(added) == 1 and added[0].linked_from and added[0].link_type and added[0].needs_strip_down
          and any(d.key == item_key(added[0].model_dump()) and d.action == "added" for d in look.decisions),
          "one list item added from a linked part, still to confirm at strip-down")
    check(look.link_run is not None and look.link_run.calls == 1, "the linked check's run")
    check(all(it.part in CANONICAL_PARTS for it in list(draft.damage_summary) + list(look.items)),
          "canonical parts only")
    check(all(it.key == item_key(it.model_dump()) for it in look.items), "each suggestion's key is prefill.item_key")
    check(all(it.link_rule and it.link_rule.startswith("L1:") and len(it.link_rule) <= 40 for it in linked)
          and all(len(it.reason or "") <= 160 and not re.search(r"\d{3,}", it.reason or "") for it in linked),
          "link_rule \"L1:...\" (<= 40 characters); reasons <= 160 characters, no 3+ digit numbers")
    check(not re.search(r"\b[A-Z]{1,3}\d{1,4}[A-Z]\b", text.replace("data:image/svg+xml;base64,", "")),
          "nothing shaped like a plate")


def main() -> int:
    init_db()
    for name, fn in (
        ("4.6: the Contract 10 settings and their parsers", settings_block),
        ("4.1: the linked-part fields, out of the drafting schema", schema_fields),
        ("4.1: links off saves byte for byte as before (goldens from feat/c9-integration)", byte_for_byte),
        ("intake/links.py (Step 0's stub)", links_stub),
        ("ai/link_check.py (Step 0's stub)", link_check_stub),
        ("the dashboard's linked-parts fixture", fixture),
    ):
        run_section(name, fn)
    print(f"\ntest_c10_base: {PASSED[0]} checks passed, {len(FAILURES)} failed")
    for section, label in FAILURES:
        print(f"  FAILED [{section}] {label}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
